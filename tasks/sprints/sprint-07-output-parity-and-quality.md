# Sprint 07 - Output Parity and Quality

**Goal:** Determine whether poor generated text comes from the tiny base model, prompt/sampling choices, or a distributed split mismatch by comparing HuggingFace-direct output against the local distributed route.
**Start:** TBD
**End:** TBD

---

## Problem Summary

Sprint 04 proved that local single-node distributed inference can execute through `facebook/opt-125m`, but sanity prompts produced unreliable factual and arithmetic output. Since `facebook/opt-125m` is a small base completion model, bad answers alone do not prove the distributed path is incorrect.

Sprint 07 should compare the same prompts, generation settings, tokenizer, and model checkpoint through:

1. direct HuggingFace local inference
2. local distributed single-node route
3. later, local distributed multi-node route after Sprint 06 resolves local multi-node serving

The first target is parity, not good chatbot behavior.

---

## In Progress

- [ ] Waiting for HuggingFace-direct baseline outputs from the user.

## Todo

- [ ] Collect HuggingFace-direct baseline outputs for the agreed prompt set.
- [ ] Run the same prompt set through the local distributed route with matching generation settings.
- [ ] Compare direct and distributed outputs for prompt relevance, token corruption, and rough continuation shape.
- [ ] Add a deterministic greedy or low-temperature next-token parity check.
- [ ] Add a local logits comparison where practical.
- [ ] Decide whether OPT-125M distributed output is acceptable as a transport smoke test only or usable as a model-quality demo.
- [ ] If parity fails, inspect architecture adapter, attention masks, position IDs, final norm, projections, dtype, and tied LM head behavior.
- [ ] If parity passes but quality is weak, document OPT-125M as a base-model limitation and prefer a better instruction model for demos.

## Done

- [ ] None yet.

---

## Acceptance Criteria

- [ ] HuggingFace-direct baseline outputs are recorded.
- [ ] Distributed-route outputs are recorded with the same prompts and settings.
- [ ] Any mismatch is classified as model limitation, sampling/prompting issue, or distributed parity bug.
- [ ] At least one deterministic parity check exists before output quality is trusted.
- [ ] Documentation states which model should be used for demos versus transport validation.

---

## Baseline Prompt Set

Use the same model, tokenizer, and generation parameters where possible:

```text
model: facebook/opt-125m
max_new_tokens: 16
temperature: 0.7
top_p: 0.95
```

Run these prompts:

```text
Hello, my name is
The capital of France is
Once upon a time
2 + 2 =
Question: What is the capital of France?
Answer:
Question: What is 2 + 2?
Answer:
```

Also run these lower-temperature variants:

```text
model: facebook/opt-125m
max_new_tokens: 8
temperature: 0.2
top_p: 0.8
```

```text
The capital of France is
2 + 2 =
```

If your HuggingFace script supports greedy generation, also run:

```text
max_new_tokens: 8
do_sample: false
```

```text
The capital of France is
2 + 2 =
```

---

## Session Log

### 2026-07-06 - Create output parity and quality sprint

- What changed: created Sprint 07 for HuggingFace-direct versus distributed-output comparison.
- Why: Sprint 04 showed the distributed route executes, but output sanity checks fail on factual/arithmetic prompts; parity must be checked before treating generated text as a correctness signal.
- Status: sprint is planned; waiting for direct HuggingFace baseline outputs.
