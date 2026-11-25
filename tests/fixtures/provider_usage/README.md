Edge-case usage fixtures derived from the two real, read-only samples under
`tests/fixtures/provider_usage_samples/` (do not edit those two files; this
directory holds derived copies and deliberately-mutated edge cases only).

Claude (`claude_result_wrapper_real_2026-11-25.json` shape, the final JSON
object of `claude -p --output-format json`):

- `claude_real.json` -- the real sample, copied verbatim.
- `claude_missing_usage.json` -- no `usage` key at all.
- `claude_missing_model_usage.json` -- `usage` present, no `modelUsage` key.
- `claude_partial_fields.json` -- `usage.input_tokens` present, `output_tokens` absent.
- `claude_wrong_types.json` -- `input_tokens` is a string, `cache_read_input_tokens` is a bool.
- `claude_negative.json` -- `output_tokens` is negative.
- `claude_huge_value.json` -- `input_tokens` exceeds the plausible-token sanity bound.
- `claude_model_usage_partial.json` -- one `modelUsage` entry with only some fields.
- `claude_not_json.txt` -- truncated/non-JSON wrapper stdout.

Codex (`codex_exec_events_real_2026-11-25.jsonl` shape, stdout of
`codex exec --json`, one JSON object per line):

- `codex_real.jsonl` -- the real sample, copied verbatim.
- `codex_missing_usage.jsonl` -- no `turn.completed` event at all.
- `codex_event_without_usage_field.jsonl` -- a `turn.completed` event present, but with no `usage` key.
- `codex_two_events_well_formed.jsonl` -- two `turn.completed` events, every
  field well-formed in both -- the sum-both rule.
- `codex_two_events_one_malformed.jsonl` -- two `turn.completed` events, the
  second missing `output_tokens` -- the per-field "unknown, not guessed" rule.
- `codex_wrong_types.jsonl` -- `usage.input_tokens` is a float.
- `codex_negative.jsonl` -- `usage.cached_input_tokens` is negative.
- `codex_truncated.jsonl` -- a truncated/non-JSON final line.
