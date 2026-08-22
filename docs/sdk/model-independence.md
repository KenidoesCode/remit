# Model independence

> **Bring your own model. REMIT decides what it is allowed to do.**

## No vendor in the SDK

There is no `RemitGPT`, no `RemitClaude`, no `RemitOpenAI`, and there will not
be. The SDK has **zero runtime dependencies** and knows nothing about any AI
provider.

The integration point is a function that returns a proposal:

```ts
async function decideWhatToBuy(humanSaid: string): Promise<string> {
  // OpenAI, Anthropic, Gemini, Llama on your own GPU, a rules engine,
  // or a deterministic stub. REMIT does not care.
  return proposal;
}

const proposal = await decideWhatToBuy(humanSaid);
const decision = await remit.authorization.evaluate({ text: proposal });
```

Every model crosses the **same** boundary, because the boundary is on the
server and does not know which model produced the input.

## Why this is a property and not a preference

REMIT's policy engine is a pure function over a compiled envelope. It performs
no I/O, has no clock of its own, and **reads no free text at all**. There is no
input through which a model could influence it beyond the envelope it already
produced.

That is what makes "model independent" checkable rather than aspirational:
swapping the interpreter cannot change what the decider does with a given
envelope. The server ships an `Interpreter` protocol with a rule-based
implementation and a real OpenAI-compatible adapter (llama.cpp, Ollama, vLLM,
LM Studio, any hosted endpoint), and `test_model_independence.py` asserts the
decision is unchanged across them.

## The model is untrusted input

A model returning this:

```json
{ "verdict": "AUTO", "authorized": true, "ceiling_paise": 99999999 }
```

gets **13 authorization-shaped fields stripped**, and the attempt is recorded
rather than silently discarded — an interpreter that keeps trying to authorise
payments is a fact you want in the audit trail.

## Why not an LLM judge

A common design puts a second model in front of the first to check its work.
That places **two persuadable systems in series** and calls it defence in depth.
A prompt that convinces the agent is often a prompt that convinces the judge,
because they share a failure mode.

REMIT's decider cannot be persuaded because it cannot read. It is 21 clauses
over structured fields, `now` is an argument, and the whole thing is a pure
function you can call in a unit test.

## What the model IS good for

Interpretation, which is genuinely hard: what did a person mean by "something
nice for my sister's birthday, nothing too expensive"? That is where a model
earns its place — and its output is then a *reading*, subject to a confidence
score and a policy, never a decision.
