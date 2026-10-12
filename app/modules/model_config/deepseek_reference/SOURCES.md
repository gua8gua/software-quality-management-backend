# DeepSeek reference assets

Downloaded 2026-10-07, reused without modification (MIT license included).

- `encoding_dsv4.py`: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/7872f01b1d1fe23eabc4c98b48bffcef5a386062/encoding/encoding_dsv4.py
  SHA256 `abc0d26120250dda0ae077dc64aa28836026e61e970854aaeb792445e6a0dde6`.
- `tokenizer.json`: extracted from https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip
  SHA256 `89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614`.
- Encoding explanation: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/7872f01b1d1fe23eabc4c98b48bffcef5a386062/encoding/README.md
- Token usage: https://api-docs.deepseek.com/quick_start/token_usage/
- Tool protocol: https://api-docs.deepseek.com/guides/tool_calls
- API and context metadata: https://api-docs.deepseek.com/api/create-chat-completion/ and https://api-docs.deepseek.com/api/list-models/

The wrapper only adapts OpenAI text content parts and attaches tools to the
reference encoder's documented system field. This is offline reference counting;
API `usage` is the authoritative count. Unknown model generations fail closed.
The vocabulary is not generative model weights. No network download at runtime.
