```
$ python scripts/bench_agentic_local_tts.py logs/2026-05-19-025122-whisper-largev3-qwen3-30b-a3b-2507-voicevox
config: whisper-largev3-qwen3-30b-a3b-2507-voicevox  model: openai:qwen/qwen3-30b-a3b-2507  base_url: http://192.168.11.16:1234/v1
run: logs/2026-05-19-025122-whisper-largev3-qwen3-30b-a3b-2507-voicevox
mode: agentic-local-tts
turns analyzed (non-warmup): 20 / 30 total

== headline ==
e2e_first_audio_s    n=19/20  p50=3.91s  p95=11.30s
agent_first_call_s   n=14/20  p50=1.84s  p95=2.24s
speak_tts_s          n=19/20  p50=1.04s  p95=1.47s  (1 turn(s) had no speak)

== all turns (n=20) ==
metric                n     mean      p50      p95      max      min
e2e_first_audio_s    19    4.692    3.909   11.303   12.056    2.526
agent_first_call_s   14    1.900    1.837    2.241    2.254    1.601
speak_tts_s          19    0.941    1.041    1.473    1.486    0.260
stt_s                20    0.209    0.217    0.253    0.253    0.161
ttft_s               20    1.290    1.295    1.449    1.723    1.091
llm_step_0_s         20    1.672    1.618    2.007    2.195    1.385
llm_step_last_s      20    1.797    1.707    2.195    5.528    1.074
llm_total_s          20    3.213    3.099    5.319    7.065    1.459
prompt_tokens         0      nan      nan      nan      nan      nan
completion_tokens     0      nan      nan      nan      nan      nan
tools_total_s        14    0.718    0.003    5.007    5.007    0.003
turn_total_s         20    3.717    3.104    9.910   10.329    1.460

== mcp_tool:* ==
metric                n     mean      p50      p95      max      min
mcp_tool:current_time    3    0.000    0.000    0.001    0.001    0.000 [low-n]
mcp_tool:execute_sport_command   10    0.001    0.001    0.001    0.001    0.001
mcp_tool:relative_move    2    0.001    0.000    0.001    0.001    0.000 [low-n]
mcp_tool:wait         2    5.001    5.001    5.001    5.001    5.001 [low-n]
```
