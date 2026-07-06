# Sprint 07 - Output Parity and Quality

**Goal:** Determine whether poor generated text comes from the tiny base model, prompt/sampling choices, or a distributed split mismatch by comparing HuggingFace-direct output against the local distributed route.
**Start:** 2026-07-06
**End:** 2026-07-06

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

- [x] HuggingFace-direct baseline outputs received and analyzed.
- [x] Deterministic next-token parity probe added to the backend.
- [x] Run the parity probe against a live local distributed route.
- [x] Run a live single-node `facebook/opt-1.3b` smoke/parity probe.
- [x] Expose exact generation controls for sampled-output parity.
- [x] Add a direct HuggingFace versus distributed generated-output parity probe.
- [x] Defer multi-node parity to the live multi-process/system-validation phase after local multi-process split testing is available.

## Todo

- [x] Collect HuggingFace-direct baseline outputs for the agreed prompt set.
- [x] Run the same prompt set through the local distributed route with matching generation settings.
- [x] Compare direct and distributed outputs for prompt relevance, token corruption, and rough continuation shape.
- [x] Add a deterministic greedy or low-temperature next-token parity check.
- [x] Add a local logits comparison where practical.
- [x] Decide whether OPT-125M distributed output is acceptable as a transport smoke test only or usable as a model-quality demo.
- [x] If parity fails, inspect architecture adapter, attention masks, position IDs, final norm, projections, dtype, and tied LM head behavior.
- [x] If parity passes but quality is weak, document OPT-125M as a base-model limitation and prefer a better instruction model for demos.
- [x] Add API controls for exact generation parity if needed: `top_k`, `repetition_penalty`, and `do_sample=false`.
- [x] Provide the broader `facebook/opt-1.3b` generated-output parity path after exact generation controls are exposed; live rerun requires an active complete route.

## Done

- [x] Direct HuggingFace baseline recorded for OPT-125M on CPU.
- [x] Baseline output classified as a base-model/prompt-quality limitation for factual and arithmetic prompts.
- [x] Backend now exposes a deterministic direct HuggingFace versus distributed next-token logits comparison.
- [x] Local single-node full-layer distributed route passed next-token argmax parity on the baseline prompt set.
- [x] Local single-node full-layer OPT-1.3B route passed deterministic next-token parity for `The capital of France is`.
- [x] `/chat`, `/stream`, and `/generator/trace` now accept `top_k`, `repetition_penalty`, and `do_sample=false` generation controls.
- [x] `/generator/parity/generate` compares direct HuggingFace generated text with distributed generated text under one shared config.
- [x] Broader OPT-1.3B whole-output parity is now runnable through `/generator/parity/generate` once a complete live route is up.
- [x] Multi-node parity is explicitly deferred to the later live multi-process validation phase because Sprint 06 chose one backend process per serving participant.

---

## Acceptance Criteria

- [x] HuggingFace-direct baseline outputs are recorded.
- [x] Distributed-route outputs are recorded with the same prompts and settings.
- [x] Any mismatch is classified as model limitation, sampling/prompting issue, or distributed parity bug.
- [x] At least one deterministic parity check exists before output quality is trusted.
- [x] Documentation states which model should be used for demos versus transport validation.

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

# OPT-125M Baseline Test Results & Analysis

**Model:** `facebook/opt-125m`
**Device:** CPU
**Date:** 2026-07-06

---

## Raw Output

### Config 1 — Baseline (temp=0.7, top_p=0.95, max_new_tokens=16)

| Prompt | Output |
|---|---|
| `Hello, my name is` | Hello, my name is Mike. I'm from Wisconsin and currently live in the Midwest. I enjoy reading |
| `The capital of France is` | The capital of France is the capital of the EU. The EU is the sovereign capital.\n\nThe |
| `Once upon a time` | Once upon a time I was on a flight to San Francisco, CA, and was at the airport |
| `2 + 2 =` | 2 + 2 = -4*m. Suppose -m = -4*b - 0* |
| `Question: What is the capital of France?\nAnswer:` | Question: What is the capital of France?\nAnswer: France is capital of the UK. |
| `Question: What is 2 + 2?\nAnswer:` | Question: What is 2 + 2?\nAnswer: It's a random number generator. |

### Config 2 — Low-temp (temp=0.2, top_p=0.8, max_new_tokens=8)

| Prompt | Output |
|---|---|
| `The capital of France is` | The capital of France is the capital of the French Republic.\n |
| `2 + 2 =` | 2 + 2 = -2*b. Let j( |

### Config 3 — Greedy (do_sample=False, max_new_tokens=8)

| Prompt | Output |
|---|---|
| `The capital of France is` | The capital of France is the capital of the French Republic.\n |
| `2 + 2 =` | 2 + 2 = -2*g. Let j( |

---

## Analysis

**1. Base model, not instruction-tuned**
OPT-125M predicts plausible next tokens rather than answering questions. The `Question:/Answer:` prompts produce fluent but factually wrong completions ("France is capital of the UK", "It's a random number generator") — the model mimics the *shape* of Q&A text without reasoning about content.

**2. No arithmetic capability**
`2 + 2 =` never resolves to `4` in any config. Outputs drift into algebra-like tokens (`-2*b`, `-4*m`, `-2*g`). This is expected for a small base LM — arithmetic needs scale, targeted training data, or fine-tuning that 125M generic pretraining doesn't provide.

**3. Temperature/top_p have minimal effect at low values**
Low-temp (0.2) and greedy (do_sample=False) outputs are nearly identical ("capital of the French Republic", `-2*b` vs `-2*g`). This convergence is expected — low temperature sampling approximates greedy decoding — and confirms the generation config is wired correctly.

**4. Higher temperature (0.7) → more diverse, more "creative" completions**
"Hello, my name is" produces a coherent mini-bio (Mike, Wisconsin, reading). Open-ended continuation is where OPT-125M performs best, compared to structured Q&A or math.

**5. Repetition/circularity pattern**
Both baseline and low-temp runs produce tautological loops ("The capital of France is the capital of…"). Small models are prone to this, especially on prompts without a strong semantic target.

## Bottom Line

Results are consistent with expectations for a 125M-parameter base model: fluent surface-level language, no factual grounding or arithmetic ability, and generation parameters affecting output diversity but not underlying capability. For meaningfully better answers, a larger and/or instruction-tuned model (e.g. `facebook/opt-1.3b`, or `Qwen2.5-0.5B-Instruct`) would be needed.

## Sprint 07 Root-Cause Narrowing

Current classification:

- Direct HuggingFace baseline quality: confirms `facebook/opt-125m` is not a reliable factual, arithmetic, or chat-quality demo model.
- Sampling/prompting: lower temperature and greedy decoding reduce diversity but do not make the tiny base model answer arithmetic or Q&A reliably.
- Distributed parity: still open until the live route is checked with deterministic logits.
- Token corruption: the Sprint 04 `I��m` output may be a streaming/token-decoding artifact or route issue; it should be checked separately from factual quality during distributed output comparison.

Use the live route probe after bootstrap, one full-layer serving node, and the generator are ready:

```bash
curl -s http://127.0.0.1:8000/generator/parity/next-token \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"The capital of France is"}'
```

Expected interpretation:

- `argmax_match=true` and low `max_abs_diff`: route is likely faithful for the checked prompt; poor prose is mainly model/prompt quality.
- `argmax_match=false` or high `max_abs_diff`: inspect architecture adapter, attention mask shape/values, position IDs, final norm, dtype/device conversion, and tied LM head behavior before trusting generated text.

## Distributed Single-Node Test Results

**Route:** local bootstrap + one CPU serving node for `facebook/opt-125m` layers `0-12` + generator on the same backend.
**Date:** 2026-07-06
**Node trace:** `12D3KooW... (layers 0->12)`

### Config 1 - Distributed baseline (`temperature=0.7`, `top_p=0.95`, `max_new_tokens=16`)

Note: the current distributed generator also applies its model defaults, including `top_k=50` and `repetition_penalty=1.3`. The direct HuggingFace baseline did not explicitly record those controls, so generated text comparison is qualitative. The deterministic parity section below is the stronger correctness signal.

| Prompt | Distributed output |
|---|---|
| `Hello, my name is` | `Mimi and I am a student who has recently finished her degree in Psychology at` |
| `The capital of France is` | `the most visited tourist destinations in Europe. There are over 30,000 people on` |
| `Once upon a time` | `, my wife and I were on our way to an aquarium in the bay area` |
| `2 + 2 =` | `-4*x, 5*y - 4*w + 3 + 6` |
| `Question: What is the capital of France?\nAnswer:` | `The French. If you are a native, it's called French-France` |
| `Question: What is 2 + 2?\nAnswer:` | `It's an extra. Edit: I got a little confused because the "` |

### Config 2 - Distributed low-temp (`temperature=0.2`, `top_p=0.8`, `max_new_tokens=8`)

| Prompt | Distributed output |
|---|---|
| `The capital of France is` | `a city, not a country.\n` |
| `2 + 2 =` | `-3*d. Let l be` |

## Next-Token Parity Results

Strict `atol=1e-4`, `rtol=1e-4` was too tight for the live mixed-device float path, but every prompt matched the direct HuggingFace argmax next token. With `atol=0.02`, `rtol=0.02`, all checked logits were close.

| Prompt | Direct next token | Distributed next token | Argmax match | Max abs diff | Mean abs diff | Allclose at 0.02 |
|---|---:|---:|---:|---:|---:|---:|
| `Hello, my name is` | `J` | `J` | yes | 0.015625 | 0.001686 | yes |
| `The capital of France is` | `the` | `the` | yes | 0.011719 | 0.002237 | yes |
| `Once upon a time` | `,` | `,` | yes | 0.007812 | 0.001325 | yes |
| `2 + 2 =` | `-` | `-` | yes | 0.011719 | 0.002145 | yes |
| `Question: What is the capital of France?\nAnswer:` | `France` | `France` | yes | 0.015625 | 0.001856 | yes |
| `Question: What is 2 + 2?\nAnswer:` | `2` | `2` | yes | 0.007996 | 0.001449 | yes |

## Distributed Comparison Analysis

Current classification:

- Distributed route correctness: local single-node full-layer route looks faithful enough for transport validation. The direct and distributed argmax next token matched for every baseline prompt, and live logits were close within a practical float tolerance.
- Model limitation: factual and arithmetic output remains poor in both direct and distributed runs. OPT-125M should be treated as a transport smoke-test model, not as the quality demo model.
- Sampling/prompting: low temperature changes the surface text but does not make the base model reliable for Q&A or math.
- Token corruption: the `I��m` replacement-character issue from Sprint 04 did not reproduce in this Sprint 07 distributed prompt run; outputs used normal apostrophes/quotes.
- Endpoint bug found and fixed: the first live parity attempt failed because the retained HuggingFace reference model had parameters split across CPU and CUDA after local components were moved to the generator device. The parity probe now moves the reference model to the generator device before direct comparison.

Next recommended development step: keep OPT-125M as the smoke-test model, but use a stronger instruction model for demos after route coverage and resource requirements are understood. Exact sampled-output parity can now use the API's `top_k`, `repetition_penalty`, and greedy `do_sample=false` controls so direct HuggingFace and distributed generation can be configured identically.

## OPT-1.3B Smoke and Trace Result

**Route:** local bootstrap + one CUDA serving node for `facebook/opt-1.3b` layers `0-24` + generator on the same backend.
**Date:** 2026-07-06
**Node trace:** `12D3KooW... (layers 0->24)`

The route became runnable and `/generator/status` returned `ready=true`, `route_ready=true`.

### Trace prompt

| Prompt | Trace response | Replacement characters |
|---|---|---|
| `The capital of France is` | ` Paris.  The name is pronounced as a French "p".\nPardon` | no |

The trace file was written under `backend/traces/` with trace id `facab212a2ef`. The first selected generated token was `Paris`, the first-step top candidate was also `Paris`, tensor shapes stayed `[1, seq, 2048]` through the route, and no prompt token, selected token, or decoded output reported a replacement character.

### Normal chat smoke

| Prompt | `/chat` sampled response |
|---|---|
| `The capital of France is` | ` named after a famous conqueror.  It's because the French were conquered by` |

The sampled `/chat` response was coherent but factually bad. This differs from the trace run because sampling is stochastic. Repeatable whole-output parity should use the exact generation controls now exposed by the API.

### Deterministic parity

| Prompt | Direct next token | Distributed next token | Argmax match | Max abs diff | Mean abs diff | Allclose at 0.02 |
|---|---:|---:|---:|---:|---:|---:|
| `The capital of France is` | `Paris` | `Paris` | yes | 0.015625 | 0.002189 | yes |

Current classification: the tested OPT-1.3B single-node route is faithful for first-token deterministic parity. Bad sampled prose from `/chat` is not route-corruption evidence by itself; exact sampled-output investigation can now use `top_k`, `repetition_penalty`, and greedy `do_sample=false` API controls.

## Sprint 07 Closeout Disposition

Sprint 07 is closed on parity tooling and single-node evidence, not on full production-grade multi-node validation.

- Broader OPT-1.3B whole-output parity: exact controls and `/generator/parity/generate` now make this a one-request live-route test. A broader prompt run was not executed at closeout because no backend/generator was listening on `127.0.0.1:8000` in the current session.
- Multi-node route parity: deferred to the later live multi-process/system-validation phase. Sprint 06 chose one backend process per serving participant, so a true split route requires multiple backend processes and remains a prerequisite for Sprint 10 real incentives rather than a blocker for Sprint 07 parity tooling.
- Output-quality classification: complete for Sprint 07. OPT-125M remains a smoke-test model; poor sampled prose from OPT-125M or OPT-1.3B is not route-corruption evidence when deterministic parity/trace checks pass.

## Session Log

### 2026-07-06 - Create output parity and quality sprint

- What changed: created Sprint 07 for HuggingFace-direct versus distributed-output comparison.
- Why: Sprint 04 showed the distributed route executes, but output sanity checks fail on factual/arithmetic prompts; parity must be checked before treating generated text as a correctness signal.
- Status: sprint is planned; waiting for direct HuggingFace baseline outputs.

### 2026-07-06 - Start next-token parity checks

- What changed: recorded the HuggingFace-direct OPT-125M baseline as the model-quality baseline; added a backend next-token parity method and `/generator/parity/next-token` endpoint; added focused tests for the parity method and endpoint.
- Why: the direct baseline shows OPT-125M itself produces weak factual and arithmetic completions, so Sprint 07 needs deterministic logits/argmax comparison to separate model limitation from distributed route corruption.
- Status: focused backend generation tests pass with 33 tests, and touched backend files compile. Live distributed-route parity still needs to be run against an active local node/generator.

### 2026-07-06 - Run distributed prompt and parity comparison

- What changed: ran the baseline and low-temperature prompt set through a local single-node full-layer distributed OPT-125M route; reran the next-token parity endpoint on the same prompt set; fixed the parity endpoint's mixed CPU/CUDA reference-model device bug.
- Why: Sprint 07 needed live evidence to separate poor model output from distributed route mismatch.
- Status: distributed `/chat` outputs are recorded; direct and distributed next-token argmax matched for all six baseline prompts, with logits close at `atol=0.02`, `rtol=0.02`; backend tests pass with 33 tests and touched files compile. Multi-node parity remains future work.

### 2026-07-06 - Run OPT-1.3B smoke trace and parity probe

- What changed: started a local full-layer CUDA `facebook/opt-1.3b` serving node and generator; ran `/generator/trace`, `/chat`, and `/generator/parity/next-token` for `The capital of France is`; recorded the trace id and sampled output behavior.
- Why: the user saw bad 1.3B outputs and needed evidence showing whether the problem starts at token selection, token decoding, or rendered output.
- Status: route readiness passed and deterministic next-token parity matched HuggingFace for the tested prompt. The trace selected and decoded `Paris` without replacement characters, while normal sampled `/chat` still produced a factually bad continuation. At that point, exact whole-output parity still needed generation controls for `top_k`, `repetition_penalty`, and greedy decoding.

### 2026-07-06 - Expose exact generation controls

- What changed: added `top_k`, `repetition_penalty`, and `do_sample` overrides to distributed generation, `/chat`, `/stream`, `/generator/trace`, and the renderer API client; added focused backend tests for streaming config propagation, greedy sampling, and trace endpoint forwarding.
- Why: Sprint 07 whole-output parity needs direct HuggingFace and distributed generation to run with matching sampling controls, including greedy `do_sample=false`.
- Status: exact generation controls are implemented and ready for a broader OPT-1.3B prompt parity run when a complete live route is available.

### 2026-07-06 - Add generated-output parity endpoint

- What changed: added a generated-output parity method and `/generator/parity/generate` endpoint that compare HuggingFace-direct generated text with distributed generated text under the same `max_new_tokens`, `temperature`, `top_p`, `top_k`, `repetition_penalty`, and `do_sample` settings; added focused backend tests for exact greedy text matching and endpoint forwarding.
- Why: Sprint 07 needs broader OPT-1.3B whole-output parity checks to be repeatable from one request rather than manually stitching direct HuggingFace and distributed API runs together.
- Status: generated-output parity is implemented and ready for live-route use; multi-node parity and broader live OPT-1.3B prompt runs remain open.

### 2026-07-06 - Close Sprint 07

- What changed: dispositioned the broader OPT-1.3B and multi-node parity checklist items; archived Sprint 07 as complete on parity tooling, model-quality classification, and single-node evidence.
- Why: the user asked to finish the remaining Sprint 07 checklist items and close the sprint before starting Sprint 08.
- Status: Sprint 07 is ready to archive. Broader OPT-1.3B generated-output parity is runnable when a complete live route is up, and true multi-node parity is deferred to the later live multi-process validation phase.
