# n8n media filename agent

`media-filename-agent.json` is an importable, inactive n8n workflow. The Python
bot will call its webhook only when media identification is needed. Telegram
polling remains in the Python bot; n8n is not a second Telegram receiver.

The workflow performs one limited job:

1. accept trusted mode/library information and an untrusted filename;
2. ask an AI model to extract a searchable title and, for a series, season and
   episode numbers;
3. validate and normalize the model output;
4. return JSON to the Python bot.

It cannot select a library, access media directories, download a file, rename a
file, run the organizers, or scan Jellyfin.

## Import and configure

1. In n8n, open **Workflows**, choose **Import from File**, and select
   `media-filename-agent.json`.
2. Open **AI Model - Configure Me** and choose an AI credential and model.
   The included node uses OpenAI as the starter. To use OpenRouter, Gemini,
   Ollama, or another provider, replace only this node with that provider's n8n
   chat-model node and connect it to **Identify Media**.
3. Open **Media Identify Webhook**. Before making the workflow public, change
   Authentication from None to Header Auth and create a secret header
   credential. A suggested header name is `X-Video-Manager-Secret`.
4. Save and activate the workflow.
5. Copy the production URL. Containers on the `media-automation` Docker network
   use `http://n8n:5678/webhook/media-identify`.

Do not put an AI API key or the webhook secret directly in the workflow JSON or
commit either value to Git. Store the AI key in n8n Credentials. The bot-side
secret will later be stored in the Video Manager `.env` file.

## Request contract

```json
{
  "request_id": "e07c703b-4178-4881-8c8f-b4e53f72392c",
  "chat_id": "123456789",
  "media_kind": "series",
  "library_key": "animation_series",
  "filename": "[AWHT] Dr. Stone S4 - 25 [480p].mkv",
  "caption": ""
}
```

Allowed `library_key` values are:

- `animation_series`
- `animation_movie` (compatibility key sent by the bot)
- `animation_movies`
- `video_series`
- `video_movie` (compatibility key sent by the bot)
- `video_movies`

`media_kind` must be `series` or `movie` and must agree with the chosen library.
The workflow treats `media_kind` and `library_key` as authoritative and never
allows the AI response to replace them.

## Response contract

```json
{
  "ok": true,
  "request_id": "e07c703b-4178-4881-8c8f-b4e53f72392c",
  "media_kind": "series",
  "library_key": "animation_series",
  "title_query": "Dr. Stone",
  "season": 4,
  "episode": 25,
  "year": null,
  "confidence": 0.96,
  "needs_user_input": false,
  "question": null
}
```

This is an identification suggestion, not permission to rename or move media.
The Python bot uses `title_query` with the existing IMDb fuzzy-search tool,
shows the result for confirmation, and falls back to its existing manual flow
if n8n is unavailable or returns `ok: false`.

## Test before connecting the bot

While the workflow is inactive, click **Listen for test event** in the Webhook
node and use the test URL shown by n8n. After activation, use the production
URL. If Header Auth is enabled, add the configured secret header.

Example from another container on `media-automation`:

```bash
curl -X POST http://n8n:5678/webhook/media-identify \
  -H 'Content-Type: application/json' \
  -H 'X-Video-Manager-Secret: replace-with-your-secret' \
  -d '{
    "request_id": "manual-test-1",
    "chat_id": "123456789",
    "media_kind": "series",
    "library_key": "animation_series",
    "filename": "[AWHT] Dr. Stone S4 - 25 [480p].mkv",
    "caption": ""
  }'
```

The workflow is intentionally imported as inactive and without credentials, so
it cannot expose a webhook or spend AI credits until it is configured.
