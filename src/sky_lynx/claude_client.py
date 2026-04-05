"""DeepInfra API client for Sky-Lynx analysis.

Wraps the OpenAI-compatible DeepInfra API with the Sky-Lynx system prompt.
"""

import os

from openai import OpenAI
from pydantic import BaseModel, Field

# Default model for analysis (DeepInfra's Claude Sonnet)
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


class Recommendation(BaseModel):
    """A single improvement recommendation."""

    title: str
    priority: str  # high, medium, low
    evidence: str
    suggested_change: str
    impact: str
    reversibility: str  # high, medium, low
    target_system: str = "claude_md"  # claude_md | pipeline | preference | routing | skill | schedule | agent
    target_agent: str | None = None
    recommendation_type: str = "other"  # voice_adjustment | framework_addition | etc.
    recommendation_id: str = ""  # set after auto-apply or PR creation for tracking


class AnalysisResult(BaseModel):
    """Structured result from Claude analysis."""

    executive_summary: str
    friction_analysis: str
    recommendations: list[Recommendation] = Field(default_factory=list)
    whats_working: str
    raw_response: str = ""


def _get_system_prompt() -> str:
    """Sky-Lynx system prompt — self-contained, no external dependencies."""
    return """You are Sky-Lynx, a continuous improvement analyst for the ST Metro ecosystem.

You analyze Claude Code usage insights, pipeline metrics, and agent telemetry to recommend
improvements across CLAUDE.md files, pipeline configuration, agent behavior, and workflow automation.

## Voice and Style

- Data-driven and evidence-based — every recommendation cites specific metrics
- Hedged language ("consider", "might benefit from", "suggest exploring") — never prescriptive
- Concise and actionable — recommendations should be implementable in one session
- Calibrated confidence — distinguish high-confidence (strong data) from exploratory (weak signal)

## Constraints

- Never recommend changes without supporting evidence from the data provided
- Never propose irreversible changes without flagging them as such
- Never recommend more than 8 changes per analysis — focus beats volume
- Always include reversibility assessment for each recommendation

## Analytical Frameworks

### Effectiveness Measurement
Track recommendation outcomes over time. Weight future recommendations toward patterns
that produced measurable improvement. Deprioritize patterns similar to past harmful changes.

### Friction Analysis
Identify recurring pain points by frequency and severity. Distinguish one-time anomalies
from systemic patterns. Root-cause friction to specific files, workflows, or configurations.

### Recommendation Prioritization
Score by: impact (how much improvement), effort (how hard to implement), reversibility
(how easy to undo), and evidence strength (how confident in the data).

## Output Structure

- **Executive Summary**: 2-3 sentence high-level assessment
- **Friction Analysis**: Breakdown of identified issues with evidence
- **Recommendations**: Prioritized list with evidence, suggested change, impact, reversibility
- **What's Working Well**: Positive patterns to reinforce

Synthesize across all data sources. Cross-reference pipeline health with usage patterns.
Identify causal relationships, not just correlations."""


def build_analysis_prompt(
    metrics_summary: str,
    friction_details: list[str],
    outcome_digest: str | None = None,
    ideaforge_digest: str | None = None,
    research_digest: str | None = None,
    telemetry_digest: str | None = None,
    taste_digest: str | None = None,
    effectiveness_digest: str | None = None,
    pipeline_health_digest: str | None = None,
    preference_digest: str | None = None,
    mission_digest: str | None = None,
    skill_digest: str | None = None,
    starscream_digest: str | None = None,
    cost_digest: str | None = None,
    agent_context_digest: str | None = None,
    agent_effectiveness_digest: str | None = None,
    model_audit_digest: str | None = None,
) -> str:
    """Build the user prompt for analysis.

    Args:
        metrics_summary: Formatted string of weekly metrics
        friction_details: List of friction detail strings
        outcome_digest: Optional digest of idea pipeline outcomes
        ideaforge_digest: Optional digest of IdeaForge market signals
        research_digest: Optional digest of research intelligence signals
        telemetry_digest: Optional digest of Data (ClaudeClaw) usage telemetry
        taste_digest: Optional digest of taste profile capture delta
        effectiveness_digest: Optional digest of past recommendation effectiveness
        preference_digest: Optional digest of ClaudeClaw preference profile state
        mission_digest: Optional digest of ClaudeClaw mission performance
        skill_digest: Optional digest of deployed skill inventory and usage
        starscream_digest: Optional digest of Starscream LinkedIn post performance
        cost_digest: Optional digest of ClaudeClaw token costs
        agent_context_digest: Optional digest of agent registry metadata
        agent_effectiveness_digest: Optional digest of agent patch effectiveness

    Returns:
        User prompt for Claude
    """
    prompt_parts = [
        "Please analyze this week's Claude Code usage data and provide improvement recommendations.",
        "",
        "## Weekly Metrics",
        metrics_summary,
        "",
    ]

    if outcome_digest:
        prompt_parts.extend([
            "## Idea Pipeline Outcomes",
            outcome_digest,
            "",
        ])

    if ideaforge_digest:
        prompt_parts.extend([
            "## IdeaForge Market Signals",
            ideaforge_digest,
            "",
        ])

    if research_digest:
        prompt_parts.extend([
            "## Research Intelligence",
            research_digest,
            "",
        ])

    if telemetry_digest:
        prompt_parts.extend([
            "## Data (ClaudeClaw) Telemetry",
            "Usage telemetry from the Telegram bot interface showing backend routing, tool usage, latency, and error patterns.",
            telemetry_digest,
            "",
        ])

    if taste_digest:
        prompt_parts.extend([
            "## Taste Profile Delta",
            "Preference signal capture from conversation corrections, rejection patterns, and enforcement rules. "
            "Use this to calibrate tone, style, and approach in recommendations.",
            taste_digest,
            "",
        ])

    if pipeline_health_digest:
        prompt_parts.extend([
            "## Metroplex Pipeline Health",
            "Automated build pipeline metrics from Metroplex (L5 autonomy coordinator). "
            "Use this data to recommend pipeline configuration changes (thresholds, caps, auto-approve settings).",
            pipeline_health_digest,
            "",
        ])

    if preference_digest:
        prompt_parts.extend([
            "## ClaudeClaw Preference Profile",
            "Current state of the live preference learning system. Assess whether preferences "
            "are converging correctly, drifting inappropriately, or missing important dimensions.",
            preference_digest,
            "",
        ])

    if mission_digest:
        prompt_parts.extend([
            "## ClaudeClaw Mission Performance",
            "Multi-agent orchestration metrics from Command Center. Identify unreliable agents, "
            "common failure modes, and opportunities to improve routing or decomposition.",
            mission_digest,
            "",
        ])

    if skill_digest:
        prompt_parts.extend([
            "## Skill Inventory & Usage",
            "Deployed Claude Code skills vs actual usage. Identify unused skills for improvement "
            "or removal, and gaps where new skills are needed.",
            skill_digest,
            "",
        ])

    if starscream_digest:
        prompt_parts.extend([
            "## Starscream LinkedIn Performance",
            "Post engagement, topic performance, and follower trends for the autonomous LinkedIn "
            "content pipeline. Use this to recommend content strategy adjustments -- topic angles "
            "to pursue or drop, post types that outperform, opener patterns that fail.",
            starscream_digest,
            "",
        ])

    if cost_digest:
        prompt_parts.extend([
            "## ClaudeClaw Token Costs",
            "Token consumption and cost patterns across agents and sessions. "
            "Flag anomalous spending and recommend efficiency improvements.",
            cost_digest,
            "",
        ])

    if agent_context_digest:
        prompt_parts.extend([
            agent_context_digest,
            "Use this to understand which agents exist and their current learning state. "
            "When recommending agent improvements, target specific agents by ID.",
            "",
        ])

    if agent_effectiveness_digest:
        prompt_parts.extend([
            agent_effectiveness_digest,
            "",
            "**IMPORTANT**: Use agent patch effectiveness data to calibrate agent recommendations. "
            "Avoid patterns similar to past 'harmful' agent patches. "
            "Favor patterns similar to past 'effective' agent patches.",
            "",
        ])

    if model_audit_digest:
        prompt_parts.extend([
            model_audit_digest,
            "",
            "**IMPORTANT**: If model audit shows critical failures, recommend evaluating "
            "alternative models for the affected pipeline component. Do NOT recommend "
            "changing model names directly -- recommend running /model-audit compare.",
            "",
        ])

    if effectiveness_digest:
        prompt_parts.extend([
            effectiveness_digest,
            "",
            "**IMPORTANT**: Use the effectiveness data above to calibrate your recommendations. "
            "Avoid patterns similar to past 'harmful' recommendations. "
            "Favor patterns similar to past 'effective' recommendations.",
            "",
        ])

    prompt_parts.append("## Friction Details")

    if friction_details:
        for detail in friction_details:
            prompt_parts.append(f"- {detail}")
    else:
        prompt_parts.append("No friction details recorded this week.")

    prompt_parts.extend(
        [
            "",
            "## Your Task",
            "",
            "1. Analyze the friction patterns and identify root causes",
            "2. Distinguish between recurring patterns and one-time anomalies",
            "3. Generate prioritized recommendations",
            "4. Note what's working well that should be reinforced",
            "",
            "For EACH recommendation, classify it with:",
            "- **target_system**: 'claude_md' (for CLAUDE.md changes), 'pipeline' (for process changes), 'preference' (for ClaudeClaw preference profile adjustments), 'routing' (for CMD agent routing weight changes), 'skill' (for skill improvements/deprecation), 'schedule' (for scheduled task cadence changes), or 'agent' (for agent config changes — Galvatron, Starscream, Ravage, Soundwave, Scourge)",
            "- **target_agent**: If target_system is 'agent', which agent (e.g., 'galvatron', 'starscream'). Required for agent recommendations.",
            "- **recommendation_type**: One of: claude_md_update, pipeline_change, constraint_addition, constraint_removal, other",
            "",
            "Format your response with clear sections for:",
            "- Executive Summary (2-3 sentences)",
            "- Friction Analysis",
            "- Recommendations (with priority, evidence, suggested change, reversibility, target_system, target_agent, recommendation_type)",
            "- What's Working Well",
        ]
    )

    return "\n".join(prompt_parts)


def parse_recommendations(response_text: str) -> list[Recommendation]:
    """Parse recommendations from Claude's response.

    Handles formats like:
    - ### High Priority
    - **R1: Title**
    - - **Evidence**: ...
    - - **Suggested Change**: ...

    Args:
        response_text: Raw response from Claude

    Returns:
        List of parsed Recommendation objects
    """
    import re

    recommendations = []
    lines = response_text.split("\n")
    current_rec: Recommendation | None = None
    current_priority = "medium"
    in_recommendations = False

    for line in lines:
        lower_line = line.lower().strip()
        stripped = line.strip()

        # Detect start of recommendations section
        if "## recommendation" in lower_line or "# recommendation" in lower_line:
            in_recommendations = True
            continue

        # Detect end of recommendations section
        if in_recommendations and (
            "## what" in lower_line and "working" in lower_line
        ):
            in_recommendations = False
            if current_rec and current_rec.title:
                recommendations.append(current_rec)
                current_rec = None
            continue

        if not in_recommendations:
            continue

        # Detect priority headers like "### High Priority"
        if stripped.startswith("###") and "priority" in lower_line:
            if "high" in lower_line:
                current_priority = "high"
            elif "medium" in lower_line:
                current_priority = "medium"
            elif "low" in lower_line:
                current_priority = "low"
            continue

        # Detect recommendation titles like "**R1: Title**" or "**Title**"
        # Match patterns: **R1: Title**, **Title**, 1. **Title**
        title_match = re.match(r'^(?:\d+\.\s*)?\*\*(?:R\d+:\s*)?(.+?)\*\*\s*$', stripped)
        if title_match:
            # Save previous recommendation
            if current_rec and current_rec.title:
                recommendations.append(current_rec)

            title = title_match.group(1).strip()
            current_rec = Recommendation(
                title=title,
                priority=current_priority,
                evidence="",
                suggested_change="",
                impact="",
                reversibility="high",
            )
            continue

        # Parse attributes within a recommendation
        if current_rec:
            # Evidence line: - **Evidence**: ...
            if "**evidence**" in lower_line:
                match = re.search(r'\*\*Evidence\*\*:\s*(.+)', line, re.IGNORECASE)
                if match:
                    current_rec.evidence = match.group(1).strip()

            # Suggested change: - **Suggested Change**: ...
            elif "**suggested change**" in lower_line:
                match = re.search(r'\*\*Suggested Change\*\*:\s*(.+)', line, re.IGNORECASE)
                if match:
                    current_rec.suggested_change = match.group(1).strip()

            # Impact line: - **Impact**: ...
            elif "**impact**" in lower_line:
                match = re.search(r'\*\*Impact\*\*:\s*(.+)', line, re.IGNORECASE)
                if match:
                    current_rec.impact = match.group(1).strip()

            # Reversibility: - **Reversibility**: High/Medium/Low
            elif "**reversibility**" in lower_line:
                if "high" in lower_line:
                    current_rec.reversibility = "high"
                elif "low" in lower_line:
                    current_rec.reversibility = "low"
                else:
                    current_rec.reversibility = "medium"

            # Target system: - **Target System**: persona/claude_md/pipeline
            elif "**target system**" in lower_line or "**target_system**" in lower_line:
                match = re.search(r'\*\*[Tt]arget[_ ][Ss]ystem\*\*:\s*(.+)', line)
                if match:
                    val = match.group(1).strip().lower()
                    if val in ("claude_md", "pipeline", "preference", "routing", "skill", "schedule", "agent"):
                        current_rec.target_system = val

            # Target agent: - **Target Agent**: galvatron
            elif "**target agent**" in lower_line or "**target_agent**" in lower_line:
                match = re.search(r'\*\*[Tt]arget[_ ][Aa]gent\*\*:\s*(.+)', line)
                if match:
                    current_rec.target_agent = match.group(1).strip()

            # Recommendation type
            elif "**recommendation type**" in lower_line or "**recommendation_type**" in lower_line:
                match = re.search(r'\*\*[Rr]ecommendation[_ ][Tt]ype\*\*:\s*(.+)', line)
                if match:
                    current_rec.recommendation_type = match.group(1).strip().lower()

    # Don't forget the last one
    if current_rec and current_rec.title:
        recommendations.append(current_rec)

    return recommendations


def analyze_insights(
    metrics_summary: str,
    friction_details: list[str],
    dry_run: bool = False,
    api_key: str | None = None,
    outcome_digest: str | None = None,
    ideaforge_digest: str | None = None,
    research_digest: str | None = None,
    telemetry_digest: str | None = None,
    taste_digest: str | None = None,
    effectiveness_digest: str | None = None,
    pipeline_health_digest: str | None = None,
    preference_digest: str | None = None,
    mission_digest: str | None = None,
    skill_digest: str | None = None,
    starscream_digest: str | None = None,
    cost_digest: str | None = None,
    agent_context_digest: str | None = None,
    agent_effectiveness_digest: str | None = None,
    model_audit_digest: str | None = None,
) -> AnalysisResult:
    """Run Claude analysis on the insights data.

    Args:
        metrics_summary: Formatted metrics summary
        friction_details: List of friction details
        dry_run: If True, skip API call and return mock result
        api_key: Optional API key override
        outcome_digest: Optional digest of idea pipeline outcomes
        ideaforge_digest: Optional digest of IdeaForge market signals
        research_digest: Optional digest of research intelligence signals
        telemetry_digest: Optional digest of Data (ClaudeClaw) usage telemetry
        taste_digest: Optional digest of taste profile capture delta
        effectiveness_digest: Optional digest of past recommendation effectiveness
        preference_digest: Optional digest of ClaudeClaw preference profile state
        mission_digest: Optional digest of ClaudeClaw mission performance
        skill_digest: Optional digest of deployed skill inventory and usage
        cost_digest: Optional digest of ClaudeClaw token costs
        agent_context_digest: Optional digest of agent registry metadata
        agent_effectiveness_digest: Optional digest of agent patch effectiveness

    Returns:
        AnalysisResult with recommendations
    """
    if dry_run:
        return AnalysisResult(
            executive_summary="[DRY RUN] No API call made.",
            friction_analysis="[DRY RUN] Would analyze friction patterns here.",
            recommendations=[
                Recommendation(
                    title="[DRY RUN] Example recommendation",
                    priority="medium",
                    evidence="Example evidence",
                    suggested_change="Example change",
                    impact="Example impact",
                    reversibility="high",
                )
            ],
            whats_working="[DRY RUN] Would identify positive patterns here.",
            raw_response="[DRY RUN]",
        )

    # Get API key
    key = api_key or os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise ValueError(
            "DEEPINFRA_API_KEY not found in environment. "
            "Set it in .env or ~/.env.shared"
        )

    client = OpenAI(
        api_key=key,
        base_url="https://api.deepinfra.com/v1/openai",
    )
    system_prompt = _get_system_prompt()
    user_prompt = build_analysis_prompt(
        metrics_summary, friction_details, outcome_digest, ideaforge_digest,
        research_digest, telemetry_digest, taste_digest, effectiveness_digest,
        pipeline_health_digest, preference_digest, mission_digest,
        skill_digest, starscream_digest, cost_digest, agent_context_digest,
        agent_effectiveness_digest, model_audit_digest,
    )

    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    # Extract text from response
    raw_response = response.choices[0].message.content or ""

    # Parse sections from response
    sections = _parse_response_sections(raw_response)
    recommendations = parse_recommendations(raw_response)

    return AnalysisResult(
        executive_summary=sections.get("executive_summary", ""),
        friction_analysis=sections.get("friction_analysis", ""),
        recommendations=recommendations,
        whats_working=sections.get("whats_working", ""),
        raw_response=raw_response,
    )


def _parse_response_sections(response: str) -> dict[str, str]:
    """Parse named sections from Claude's response.

    Args:
        response: Raw response text

    Returns:
        Dict mapping section names to content
    """
    sections = {
        "executive_summary": "",
        "friction_analysis": "",
        "whats_working": "",
    }

    current_section: str | None = None
    current_content: list[str] = []

    for line in response.split("\n"):
        lower_line = line.lower()

        # Detect section headers
        if "executive" in lower_line and "summary" in lower_line:
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = "executive_summary"
            current_content = []
        elif "friction" in lower_line and "analysis" in lower_line:
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = "friction_analysis"
            current_content = []
        elif "working well" in lower_line:
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = "whats_working"
            current_content = []
        elif "recommendation" in lower_line and line.startswith("#"):
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = None
            current_content = []
        elif current_section:
            # Skip section headers
            if not line.startswith("#"):
                current_content.append(line)

    # Save last section
    if current_section and current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections
