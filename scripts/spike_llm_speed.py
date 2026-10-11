"""SPIKE 3: LLM speed. Measures time-to-first-token and tokens/s on a ~2k-token prompt via Ollama.
  ollama pull qwen2.5:3b   (or any model)   then:   python spike_llm_speed.py qwen2.5:3b"""
import json, sys, time, urllib.request
model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:3b"
prompt = ("The quick brown fox jumps over the lazy dog. " * 180) + "\nSummarise the text above in one sentence."
req = urllib.request.Request("http://localhost:11434/api/generate", method="POST",
                             data=json.dumps({"model": model, "prompt": prompt, "stream": True, "options": {"num_ctx": 4096}}).encode())
t0, first, n = time.time(), None, 0
with urllib.request.urlopen(req, timeout=600) as r:
    for line in r:
        d = json.loads(line)
        if d.get("response"):
            n += 1
            first = first or time.time()
        if d.get("done"):
            break
t1 = time.time()
print(f"model={model} TTFT={first - t0:.2f}s tokens={n} tok/s={n / max(t1 - first, 1e-6):.1f}")
