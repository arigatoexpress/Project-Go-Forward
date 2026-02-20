# Tex Agent — ADK Best Practices & Configuration Guide

> Reference from Google Cloud ADK course. All patterns are implemented in `root_agent.py` and `config.yaml`.

---

## 1. Instruction Writing — 5 Reusable Patterns

Every agent instruction should include these five sections:

| Pattern | Purpose | Example |
|---------|---------|---------|
| **Identity** | Who the agent is | `You are [Name], a [Role] with [Experience]` |
| **Mission** | What the agent does | `Guide customers from browsing to booking` |
| **Methodology** | How the agent works | `1. Understand needs 2. Search inventory 3. Book appointments` |
| **Boundaries** | Never/Always lists | `Never: share PII, calculate payments. Always: verify warranty first` |
| **Few-Shot Examples** | Input/Output pairs | `Customer: "3 bed under 80k" → Search with beds=3, max_price=80000` |

Use markdown structure for readability. Create generic templates with `[placeholders]` for domain flexibility.

---

## 2. Structured Output — Pydantic Schemas

**Key insight**: Structured output bridges natural language AI and system integration. The schema is a contract — define exactly what you need.

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class ServiceResponse(BaseModel):
    response: str = Field(description="The response to the user")
    action_required: bool = Field(description="Whether action is needed")
    metadata: dict = Field(default={}, description="Additional metadata")

LlmAgent(
    model="gemini-2.5-flash",
    instruction="...",
    output_schema=ServiceResponse,  # Forces JSON output matching this schema
)
```

Rules:
- Use `Pydantic BaseModel`, never raw dictionaries
- Include `Field(description=...)` to guide the LLM
- **Only defined fields appear in output** — include ALL needed fields
- Use `output_key` for passing data between agents in workflows
- Handle optional fields with `Optional[T]` and defaults
- **Do NOT apply `output_schema` to conversational agents** (breaks markdown responses)

Our schemas live in `schemas/output_schemas.py`.

---

## 3. Model Selection & Configuration

### Strategy
1. **Start with Gemini 2.5 Pro**: Prototyping, quality baselines, complex reasoning
2. **Optimize with Gemini 2.5 Flash**: Production, high-volume, cost optimization
3. **Always perform gap analysis** when switching Pro to Flash

### Temperature Guide
| Range | Use Case |
|-------|----------|
| 0.0–0.3 | Facts, data extraction, analysis, consistency |
| 0.4–0.7 | Balanced, general assistance, customer support |
| 0.8–1.0 | Creative writing, brainstorming, marketing |

### Safety Thresholds
| Level | Use Case |
|-------|----------|
| `BLOCK_LOW_AND_ABOVE` | Strictest — children, public-facing |
| `BLOCK_MEDIUM_AND_ABOVE` | Standard — business, general use |
| `BLOCK_ONLY_HIGH` | Relaxed — research, internal tools |

### GenerateContentConfig (in `config.yaml`)
```yaml
agent:
  model: "gemini-2.5-flash"
  model_config:
    temperature: 0.7
    max_output_tokens: 2048
    top_p: 0.95
    top_k: 40
  safety_level: "block_low_and_above"
```

```python
from google.genai import types

config = types.GenerateContentConfig(
    temperature=0.7,
    max_output_tokens=2048,
    safety_settings=[
        types.SafetySetting(
            category="HARM_CATEGORY_DANGEROUS_CONTENT",
            threshold="BLOCK_MEDIUM_AND_ABOVE",
        )
    ],
)

LlmAgent(model="gemini-2.5-flash", generate_content_config=config, ...)
```

---

## 4. Planning for Complex Tasks

**Key insight**: Planning transforms reactive agents into strategic thinkers. Simple instructions work — planning adds structured reasoning automatically.

### When to Use What
| Planner | When |
|---------|------|
| `BuiltInPlanner` | Gemini models, multi-step tasks, dependencies |
| `PlanReActPlanner` | Non-Gemini models with structured planning |
| No planner | Simple single-step tasks |

### Configuration (in `config.yaml`)
```yaml
agent:
  thinking:
    enabled: true
    include_thoughts: true    # true for debugging, false for production
    thinking_budget: 1024     # 512-2048 tokens typical
```

```python
from google.adk.planners import BuiltInPlanner
from google.genai import types

planner = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(
        include_thoughts=True,
        thinking_budget=1024,
    )
)

LlmAgent(model="gemini-2.5-flash", planner=planner, ...)
```

**Tip**: Use lower temperature (0.2–0.3) with planning for systematic thinking.

---

## 5. Code Patterns

### Pattern 1: Professional Service Agent
```python
LlmAgent(
    model="gemini-2.5-flash",
    name="service_pro",
    instruction="""
    # PERSONA
    You are [Name], a [Role] with [Experience].
    # BOUNDARIES
    Never: [List of restrictions]
    Always: [List of requirements]
    # EXAMPLES
    [Few-shot examples]
    """,
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=1024)
    ),
    output_schema=ServiceResponse,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.5,
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_MEDIUM_AND_ABOVE",
            )
        ],
    ),
)
```

### Pattern 2: Data Extraction Pipeline
```python
class ExtractionOutput(BaseModel):
    entities: List[Entity] = Field(description="Extracted entities")
    relationships: List[str] = Field(description="Relationships found")

LlmAgent(
    model="gemini-2.5-flash",
    name="data_extractor",
    instruction="Extract structured data from text",
    output_schema=ExtractionOutput,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)
```

### Pattern 3: Decision Making System
```python
LlmAgent(
    model="gemini-2.5-pro",
    name="decision_maker",
    instruction="Evaluate requests against policy. Consider all factors and dependencies.",
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(include_thoughts=False, thinking_budget=2048)
    ),
    output_key="approved",
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
```

---

## 6. Common Pitfalls

### Instructions
- Vague personas without specific expertise
- Missing boundaries leading to off-brand responses
- No few-shot examples causing inconsistent behavior
- Over-complex instructions that confuse the model

### Planning
- Not using planner for complex multi-step tasks
- Using wrong planner type (`BuiltInPlanner` for non-Gemini models)
- Over-planning simple single-step tasks
- High temperature with planning (reduces systematic thinking)

### Configuration
- Starting with Flash instead of Pro for prototyping (wrong baseline)
- Using Pro for high-volume simple tasks (wastes money)
- Wrong safety settings for audience
- Temperature too high for factual tasks (causes hallucinations)

### Output
- Using dictionaries instead of Pydantic BaseModel
- Not defining all needed fields (only defined fields appear)
- Missing `Field(description=...)` to guide the LLM
- Not validating output before use in production

---

## 7. Our Implementation

| Component | File | Status |
|-----------|------|--------|
| Instruction patterns | `root_agent.py` | Identity, Mission, Methodology, Boundaries per agent |
| GenerateContentConfig | `root_agent.py` → `_build_generate_content_config()` | temp=0.7, safety=4 categories |
| BuiltInPlanner | `root_agent.py` → `_build_planner()` | Root agent only, budget=1024 |
| Output schemas | `schemas/output_schemas.py` | Defined, not applied to chat agents |
| Config source | `config.yaml` → `model_config` + `thinking` sections | All values config-driven |
| Config loader | `config_loader.py` → `get_model_config()`, `get_thinking_config()` | Cached accessors |

---

## References

- [ADK LLM Agent docs](https://google.github.io/adk-docs/agents/llm-agents/)
- [Gemini model docs](https://ai.google.dev/gemini-api/docs)
- [Gemini safety settings](https://ai.google.dev/gemini-api/docs/safety-settings)
- [Vertex AI pricing](https://cloud.google.com/vertex-ai/pricing)
- [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
