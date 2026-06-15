# Submitting

The agent runs on a **real LLM** — set a key or a local endpoint first:
```bash
export OPENAI_API_KEY=sk-...                 # cloud (default model gpt-5.4-nano)
# OR free local: run Ollama/llama.cpp and set provider:"local" in config.json + LOCAL_BASE_URL
```

1. **Self-check** your scaffold (pure stdlib, no key):
   ```bash
   python harness/selfcheck.py
   ```
2. **Run the simulator** for the current phase:
   ```bash
   ./bin/<phase>/observathon-sim --config solution/config.json --wrapper solution/wrapper.py \
       --out run_output.json --concurrency 8
   #   <phase> = practice | public | private
   #   macOS first run: xattr -dr com.apple.quarantine bin/<phase>/*
   ```
3. **Score** the run (public/private phases):
   ```bash
   ./bin/<phase>/observathon-score --run run_output.json --findings solution/findings.json \
       --team Vu-Hai-Tuan --out score.json
   ```
4. **Commit & push** your `solution/` (config.json, prompt.txt, examples.json, wrapper.py,
   findings.json, your logs/traces) + `run_output.json` + `score.json`:
   ```bash
   git add solution/ run_output.json score.json && git commit -m "Vu-Hai-Tuan public" && git push
   ```

## Binaries by OS (`bin/<phase>/`)
| OS / arch | file |
|---|---|
| macOS (Apple Silicon, M1+) | `observathon-sim` / `observathon-score` (arm64) |
| Windows | `observathon-sim.exe` / `observathon-score.exe` |
| Linux | `observathon-sim` / `observathon-score` (x86_64) |

(macOS Intel is not pre-built — Apple-Silicon, Windows and Linux are provided; on Intel,
run from source with Python + `openai`.)

## Phases
practice @ T0 → public **sim** @ T+1h, **score** @ T+2h → private **sim** @ T+3h, **score** @ T+3.5h.
The **private** phase adds a held-out, paraphrased set + the **injection** twist. Push your
private result once.

## Commands used by Team Vu-Hai-Tuan

The binaries in this workspace are currently placed at the repository root. On macOS, remove
the quarantine attribute once:

```bash
xattr -d com.apple.quarantine observathon-sim 2>/dev/null || true
xattr -d com.apple.quarantine observathon-score 2>/dev/null || true
```

Validate the solution and wrapper tests:

```bash
python3 harness/selfcheck.py
python3 -m unittest -v harness.test_wrapper
```

Run and score the public set:

```bash
export OPENAI_API_KEY="sk-..."

./observathon-sim --testset public \
  --config solution/config.json \
  --wrapper solution/wrapper.py \
  --out run_output_openai.json \
  --concurrency 2

./observathon-score \
  --run run_output_openai.json \
  --findings solution/findings.json \
  --team Vu-Hai-Tuan \
  --out score.json
```

Current public result: **100.0/100**, `diag_f1=1.0`, `120/120` requests completed without an
agent error. Do not commit or publish the API key.

Commit the submission artifacts:

```bash
git add solution/ run_output_openai.json score.json README.md docs/SUBMIT.md \
  submission/TEMPLATE_FINDINGS.md
git commit -m "Vu-Hai-Tuan public"
git push
```
