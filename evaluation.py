"""Evaluate a saved voice-interview conversation with an LLM.

Fetches the latest conversation for a user_id from the ``vgi_conversation``
collection, sends the questions + answers to the LLM, and produces a scored
report matching the frontend schema (overall, dimensions, questionReviews).

The LLM judges at an easy-to-medium level: if an answer shows the right intent
and understanding, it scores well; weaker or missing answers score lower.

Cosmetic/derived fields (band, percentile, certification, delivery, rewards,
nextSteps, ...) are left for the frontend to manage.

Run it:
  uv run python evaluation.py <user_id>
  uv run python evaluation.py 127
"""

import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
CONVERSATION_COLLECTION = "vgi_conversation"
MODEL = "gpt-4o-mini"

# The four scoring dimensions and their weights (max points sum to 100).
DIMENSIONS = {
    "technical": 40,
    "depth": 25,
    "communication": 20,
    "problemSolving": 15,
}
QUESTION_MAX_SCORE = 40  # per-question max (matches the frontend example)

SYSTEM_PROMPT = """You are an experienced but fair technical interviewer scoring a \
voice interview. You are given the questions asked and the candidate's spoken answers.

Judge at an EASY-TO-MEDIUM level, focusing on INTENT and understanding rather than \
perfect wording:
- If an answer shows the candidate genuinely understands the concept and is heading in \
the right direction, score it well even if it is not textbook-perfect or is a bit rough \
(these are spoken, informal answers).
- Give partial credit for partially correct or incomplete answers.
- Score low for answers that are wrong, empty, "I don't know", or off-topic.
- Do not penalize small grammatical issues or filler words from speech.

Score everything as a PERCENT from 0 to 100. Return a JSON object with EXACTLY this shape:
{
  "dimensions": {
    "technical": <0-100>,        // correctness of technical content
    "depth": <0-100>,            // depth of understanding and detail
    "communication": <0-100>,    // clarity and structure of the answers
    "problemSolving": <0-100>    // reasoning and approach
  },
  "questionReviews": [
    {
      "percent": <0-100>,        // how good this specific answer was
      "good": "<one short sentence on what was good>",
      "improve": "<one short sentence on what to improve>"
    }
  ]
}
Include ONE entry in "questionReviews" for EACH question, in the order asked.
"""


def fetch_conversation(user_id: str) -> dict:
    """Return the most recent conversation document for a user_id."""
    client = MongoClient(DATABASE_URL, serverSelectionTimeoutMS=8000)
    try:
        doc = client.get_default_database()[CONVERSATION_COLLECTION].find_one(
            {"user_id": user_id}, sort=[("created_at", -1)]
        )
    finally:
        client.close()
    if not doc:
        raise RuntimeError(f"no conversation found for user_id={user_id!r}")
    return doc


def format_transcript(conversation: list[dict]) -> str:
    """Render the Q/A pairs into plain text for the LLM."""
    lines = []
    for i, turn in enumerate(conversation, 1):
        label = "Follow-up" if turn.get("type") == "followup" else "Question"
        lines.append(f"{label} {i}: {turn.get('question', '')}")
        lines.append(f"Answer {i}: {turn.get('answer') or '(no answer)'}\n")
    return "\n".join(lines)


def evaluate(conversation: list[dict]) -> dict:
    """Ask the LLM to score the conversation; returns raw dimension/question percents."""
    client = OpenAI()  # uses OPENAI_API_KEY
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_transcript(conversation)},
        ],
    )
    return json.loads(resp.choices[0].message.content)


def build_dimensions(percents: dict) -> dict:
    """Turn the LLM's per-dimension percents into {points, maxPoints, percent}."""
    dimensions = {}
    for name, max_points in DIMENSIONS.items():
        percent = int(percents.get(name, 0))
        dimensions[name] = {
            "points": round(percent / 100 * max_points),
            "maxPoints": max_points,
            "percent": percent,
        }
    return dimensions


def build_question_reviews(conversation: list[dict], reviews: list[dict]) -> list[dict]:
    """Combine the conversation's questions/answers with the LLM's per-question scores."""
    out = []
    for i, (turn, review) in enumerate(zip(conversation, reviews), 1):
        percent = int(review.get("percent", 0))
        out.append({
            "questionId": f"q{i}",
            "question": turn.get("question", ""),
            "score": round(percent / 100 * QUESTION_MAX_SCORE),
            "maxScore": QUESTION_MAX_SCORE,
            "percent": percent,
            "feedback": {
                "good": review.get("good", ""),
                "improve": review.get("improve", ""),
            },
        })
    return out


# Frontend-managed / cosmetic fields we do NOT compute here. They are kept in the
# output as placeholders (the frontend fills or overrides them). Only overall.score,
# dimensions, questionReviews, sessionType and completedAt are computed below.
FRONTEND_PLACEHOLDERS = {
    "topic": "",
    "interviewType": "",
    "interviewTypeLabel": "",
    "intensity": "",
    "interviewer": "Maya",
    "overall_extra": {
        "band": "",
        "bandLabel": "",
        "scoreColor": "",
        "percentile": 0,
        "percentileText": "",
        "nationalRankLabel": "",
    },
    "certification": {"earned": False, "certificateId": "", "downloadUrl": ""},
    "delivery": {
        "paceWpm": 0, "paceLabel": "",
        "fillerCount": 0, "fillerLabel": "",
        "confidenceScore": 0, "confidenceLabel": "",
    },
    "nextSteps": [],
    "rewards": {"xpEarned": 0, "creditsEarned": 0, "streakDays": 0},
}


def build_report(user_id: str) -> dict:
    """Fetch, evaluate, and assemble the full report for a user_id.

    Computed: overall.score, dimensions, questionReviews, sessionType, completedAt.
    Everything else is a frontend-managed placeholder (see FRONTEND_PLACEHOLDERS)."""
    doc = fetch_conversation(user_id)
    conversation = doc["conversation"]
    result = evaluate(conversation)

    dimensions = build_dimensions(result.get("dimensions", {}))
    overall_score = sum(dim["points"] for dim in dimensions.values())

    completed_at = doc.get("created_at") or datetime.now(timezone.utc)
    ph = FRONTEND_PLACEHOLDERS
    return {
        "userId": user_id,
        "success": True,
        "message": "Evaluation loaded",
        "data": {
            "interviewId": doc.get("interview_id"),
            "topic": ph["topic"],
            "interviewType": ph["interviewType"],
            "interviewTypeLabel": ph["interviewTypeLabel"],
            "intensity": ph["intensity"],
            "interviewer": ph["interviewer"],
            "sessionType": "Voice Interview",
            "completedAt": completed_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "completedAtLabel": f"{completed_at:%B} {completed_at.day}, {completed_at.year}",
            "overall": {"score": overall_score, "maxScore": 100, **ph["overall_extra"]},
            "certification": ph["certification"],
            "dimensions": dimensions,
            "delivery": ph["delivery"],
            "questionReviews": build_question_reviews(
                conversation, result.get("questionReviews", [])
            ),
            "nextSteps": ph["nextSteps"],
            "rewards": ph["rewards"],
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: uv run python evaluation.py <user_id>")
        sys.exit(1)
    report = build_report(sys.argv[1])
    print(json.dumps(report, indent=2))
