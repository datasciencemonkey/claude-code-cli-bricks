"""Grocery Data Quiz — Interactive Databricks App for the Coles Vibe Coding Workshop."""

from __future__ import annotations

import asyncio
import os
import re
import time
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Grocery Data Quiz")


# Team names are rendered in the shared host/player UI. Restrict to a safe
# alphabet so a malicious attendee cannot submit markup that other players'
# browsers execute when the leaderboard re-renders.
TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,30}$")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    # Defense-in-depth: even if a future change reintroduces an innerHTML
    # interpolation without escaping, inline script execution is blocked.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

TIMER_SECONDS = int(os.getenv("QUIZ_TIMER_SECONDS", "30"))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    LOBBY = "lobby"
    QUESTION = "question"
    REVEALED = "revealed"
    RESULTS = "results"


class Question(BaseModel):
    category: str
    question: str
    options: list[str]
    correct: int
    explanation: str


class Team(BaseModel):
    name: str
    score: int = 0
    answers: list[int | None] = []  # index of chosen option per question, None if unanswered
    times: list[float | None] = []  # seconds remaining when answered


QUESTIONS: list[Question] = [
    # ------------------------------------------------------------------
    # ROUND 1: GROCERY & RETAIL (estimation / gut-feel)
    # ------------------------------------------------------------------
    Question(
        category="ESTIMATION",
        question="Australians spend ~$15B/month on food retail. What's the approximate per-capita weekly grocery spend?",
        options=["$90", "$170", "$230", "$310"],
        correct=2,
        explanation="$15B/month ÷ 26M people ÷ 4.3 weeks ≈ $134 per person — but that's individuals. Per household (~10M) it's ~$350/week. Per capita including eating out: ~$230.",
    ),
    Question(
        category="DATA PUZZLE",
        question="NSW has the highest food retail turnover. But which state has the highest turnover per capita?",
        options=["ACT", "NSW", "Western Australia", "Northern Territory"],
        correct=0,
        explanation="ACT punches above its weight — high average incomes + no nearby regional alternatives = highest per-capita food spend. NSW wins on raw volume but not per-person.",
    ),
    Question(
        category="GOTCHA",
        question="You run a pipeline that ingests ABS retail data monthly. March 2024 shows a 40% spike in NSW food turnover. What's the most likely cause?",
        options=[
            "A genuine demand surge",
            "Easter fell in March that year",
            "ABS revised historical data",
            "Seasonal adjustment wasn't applied",
        ],
        correct=3,
        explanation="ABS publishes both seasonally adjusted AND original series. A 40% spike in original data is normal — Easter timing shifts, school holidays, etc. Always check which series you're ingesting.",
    ),
    # ------------------------------------------------------------------
    # ROUND 2: AI & SYCOPHANCY (tricky / counterintuitive)
    # ------------------------------------------------------------------
    Question(
        category="AI TRAPS",
        question="Stanford tested 11 LLMs (2026): when the user was 100% wrong, how often did the AI still agree?",
        options=["12% of the time", "28% of the time", "51% of the time", "73% of the time"],
        correct=2,
        explanation="51% — worse than a coin flip. RLHF optimises for user satisfaction, not truth. This is why structural verification (BDD, schema contracts) matters more than asking 'did this work?'",
    ),
    Question(
        category="AGENT PITFALLS",
        question="You ask an AI agent to 'build a data pipeline.' It produces 12 files, a custom logging framework, and an abstract base class hierarchy. What went wrong?",
        options=[
            "The model is too powerful",
            "You didn't constrain scope in CLAUDE.md",
            "The context window was too large",
            "Nothing — that's good engineering",
        ],
        correct=1,
        explanation="Overeagerness is the #1 agent pitfall. Without constraints like 'minimal solution, no abstractions unless asked,' agents default to over-engineering. One line in CLAUDE.md prevents this.",
    ),
    Question(
        category="CRITICAL THINKING",
        question="Your agent says 'I've verified the pipeline works correctly.' What should you do?",
        options=[
            "Trust it — it ran the code",
            "Ask it to show the git diff and test output",
            "Ask it to argue why the pipeline might be WRONG",
            "Both B and C",
        ],
        correct=3,
        explanation="Never trust claims — demand proof (git diff, test results). Then apply the Karpathy Test: ask the model to argue the opposite. If it can demolish its own work, the work wasn't solid.",
    ),
    # ------------------------------------------------------------------
    # ROUND 3: DATA ENGINEERING (technical / applied)
    # ------------------------------------------------------------------
    Question(
        category="DATA ENGINEERING",
        question="Your Bronze table has 500M rows. You add CLUSTER BY (date, store_id) to the Gold table. What does this actually do on serverless?",
        options=[
            "Creates partitioned folders on disk",
            "Sorts data within files for faster predicate pushdown",
            "Creates an index like a traditional database",
            "Nothing — CLUSTER BY is ignored on serverless",
        ],
        correct=1,
        explanation="CLUSTER BY uses liquid clustering — it colocates data within files (Z-ordering) so queries with predicates on those columns skip irrelevant file groups. It's NOT partitioning.",
    ),
    Question(
        category="PIPELINE GOTCHA",
        question="Your streaming table reads from Auto Loader. You deploy, it works. Next day: 0 new rows. The source files are there. What's the most likely cause?",
        options=[
            "The checkpoint was corrupted",
            "The source path changed and Auto Loader's checkpoint tracks the OLD path",
            "Serverless compute timed out",
            "Unity Catalog permissions were revoked",
        ],
        correct=1,
        explanation="Auto Loader checkpoints are path-specific. If someone moved the source files or changed the path in config, the checkpoint still watches the old location. Classic 'silent zero rows' bug.",
    ),
    Question(
        category="DATA QUALITY",
        question="You add @dp.expect_or_fail('valid_amount', 'amount > 0') to your Silver table. 3 rows fail. What happens?",
        options=[
            "The 3 rows are dropped, rest succeeds",
            "The entire pipeline update fails",
            "The 3 rows are quarantined to an error table",
            "A warning is logged but all rows pass through",
        ],
        correct=1,
        explanation="expect_or_fail is strict — ANY row failing the constraint causes the entire update to fail. Use @dp.expect (warn only) or @dp.expect_or_drop (filter) for softer handling.",
    ),
    # ------------------------------------------------------------------
    # ROUND 4: YOUR OWN PLATFORM (Coles-specific)
    # ------------------------------------------------------------------
    Question(
        category="YOUR PLATFORM",
        question="How many active tables does Coles have under Unity Catalog management right now?",
        options=["~3,000", "~10,000", "~30,000", "~75,000"],
        correct=2,
        explanation="30,000+ active tables under UC governance as of March 2026. UC replaced Amundsen + Apache Atlas + JanusGraph as Coles' data governance layer.",
    ),
    Question(
        category="YOUR PLATFORM",
        question="What percentage of Coles' Databricks compute (DBUs) currently runs on Unity Catalog?",
        options=["32%", "55%", "71%", "94%"],
        correct=2,
        explanation="71% of DBU consumption is on UC, against a target of 80%. The gap is mostly ETL workloads still on Classic compute — migrating those to UC-native pipelines is an active initiative.",
    ),
    # ------------------------------------------------------------------
    # ROUND 5: ESTIMATION & DEBATE (fun / no clear right answer)
    # ------------------------------------------------------------------
    Question(
        category="ESTIMATION",
        question="How many unique products does a typical Coles supermarket carry?",
        options=["~8,000", "~20,000", "~35,000", "~55,000"],
        correct=1,
        explanation="A standard Coles carries ~20,000-25,000 SKUs. A Coles Local might have ~8,000. Costco carries ~4,000. For context, a single ABS category like 'bread & cereals' covers thousands of these.",
    ),
    Question(
        category="DEBATE",
        question="An AI agent writes code 10x faster than a human. Measured studies show AI-authored code has 1.7x more major issues. Net effect on team velocity?",
        options=[
            "Massive improvement — speed outweighs bugs",
            "Roughly break-even after accounting for review and fixes",
            "Net negative — bugs compound faster than speed gains",
            "Depends entirely on the review process",
        ],
        correct=3,
        explanation="This is the real answer. Speed without verification is just faster bugs. With BDD gates, CLAUDE.md constraints, and code review — the 10x speed compounds positively. Without them, it compounds negatively.",
    ),
    Question(
        category="WILDCARD",
        question="In 2024, Australia recalled 847 food products (FSANZ data). Which category had the most recalls?",
        options=[
            "Dairy & eggs",
            "Undeclared allergens in processed foods",
            "Meat & poultry contamination",
            "Foreign objects in bakery products",
        ],
        correct=1,
        explanation="Undeclared allergens dominate food recalls (~40%). It's a labelling and supply chain data problem — exactly the kind of thing a well-governed data pipeline can help detect.",
    ),
]


# ---------------------------------------------------------------------------
# In-memory quiz state
# ---------------------------------------------------------------------------

class QuizState:
    def __init__(self) -> None:
        self.phase: Phase = Phase.LOBBY
        self.current_question: int = 0
        self.teams: dict[str, Team] = {}
        self.question_start_time: float = 0.0
        self._version: int = 0  # bumped on every mutation for SSE change detection

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-serialisable state for clients."""
        q = QUESTIONS[self.current_question] if self.current_question < len(QUESTIONS) else None
        elapsed = time.time() - self.question_start_time if self.phase == Phase.QUESTION else 0
        time_left = max(0, TIMER_SECONDS - int(elapsed))

        # Leaderboard sorted by score desc, then by fastest average answer time
        leaderboard = sorted(
            self.teams.values(),
            key=lambda t: (-t.score, -sum(x or 0 for x in t.times)),
        )

        result: dict[str, Any] = {
            "phase": self.phase.value,
            "currentQuestion": self.current_question,
            "totalQuestions": len(QUESTIONS),
            "timeLeft": time_left,
            "timerSeconds": TIMER_SECONDS,
            "version": self._version,
            "leaderboard": [
                {"name": t.name, "score": t.score, "answered": len([a for a in t.answers if a is not None])}
                for t in leaderboard
            ],
            "teamCount": len(self.teams),
        }

        if q:
            result["question"] = {
                "category": q.category,
                "text": q.question,
                "options": q.options,
            }
            if self.phase == Phase.REVEALED:
                result["question"]["correct"] = q.correct
                result["question"]["explanation"] = q.explanation

        return result

    def bump(self) -> None:
        self._version += 1


quiz = QuizState()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class JoinRequest(BaseModel):
    name: str


class AnswerRequest(BaseModel):
    team: str
    answer: int


@app.post("/api/teams")
def join_team(req: JoinRequest) -> dict[str, Any]:
    name = req.name.strip()[:30]
    if not name:
        raise HTTPException(400, "Team name required")
    if not TEAM_NAME_RE.match(name):
        raise HTTPException(
            400,
            "Team name must be 1-30 characters of letters, digits, spaces, "
            "hyphens, or underscores.",
        )
    if name not in quiz.teams:
        quiz.teams[name] = Team(name=name)
        quiz.bump()
    return {"ok": True, "name": name}


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return quiz.snapshot()


@app.post("/api/answer")
def submit_answer(req: AnswerRequest) -> dict[str, Any]:
    if quiz.phase != Phase.QUESTION:
        raise HTTPException(400, "Not accepting answers right now")

    team = quiz.teams.get(req.team)
    if not team:
        raise HTTPException(404, "Team not found")

    qi = quiz.current_question
    # Pad answers list if needed
    while len(team.answers) <= qi:
        team.answers.append(None)
        team.times.append(None)

    if team.answers[qi] is not None:
        return {"ok": True, "already_answered": True}

    elapsed = time.time() - quiz.question_start_time
    time_remaining = max(0, TIMER_SECONDS - elapsed)

    team.answers[qi] = req.answer
    team.times[qi] = time_remaining

    if req.answer == QUESTIONS[qi].correct:
        # Score: base 100 + up to 100 bonus for speed
        speed_bonus = int(100 * (time_remaining / TIMER_SECONDS))
        team.score += 100 + speed_bonus

    quiz.bump()
    return {"ok": True, "already_answered": False}


@app.post("/api/control/{action}")
def control(action: str) -> dict[str, Any]:
    if action == "start":
        quiz.phase = Phase.QUESTION
        quiz.current_question = 0
        quiz.question_start_time = time.time()
        # Reset all teams
        for t in quiz.teams.values():
            t.score = 0
            t.answers = []
            t.times = []
        quiz.bump()
    elif action == "next":
        if quiz.phase == Phase.REVEALED:
            quiz.current_question += 1
            if quiz.current_question >= len(QUESTIONS):
                quiz.phase = Phase.RESULTS
            else:
                quiz.phase = Phase.QUESTION
                quiz.question_start_time = time.time()
            quiz.bump()
    elif action == "reveal":
        if quiz.phase == Phase.QUESTION:
            quiz.phase = Phase.REVEALED
            quiz.bump()
    elif action == "reset":
        quiz.phase = Phase.LOBBY
        quiz.current_question = 0
        quiz.teams.clear()
        quiz.bump()
    else:
        raise HTTPException(400, f"Unknown action: {action}")

    return quiz.snapshot()


# ---------------------------------------------------------------------------
# SSE — real-time event stream
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def events(request: Request) -> EventSourceResponse:
    last_version = -1

    async def generate():
        nonlocal last_version
        while True:
            if await request.is_disconnected():
                break
            if quiz._version != last_version:
                last_version = quiz._version
                yield {"event": "state", "data": str(quiz.snapshot()).replace("'", '"')}
            await asyncio.sleep(0.5)

    return EventSourceResponse(generate())


# ---------------------------------------------------------------------------
# Static files + SPA fallback
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
@app.get("/play", response_class=HTMLResponse)
@app.get("/host", response_class=HTMLResponse)
async def spa(request: Request) -> HTMLResponse:
    with open("static/index.html") as f:
        return HTMLResponse(f.read())
