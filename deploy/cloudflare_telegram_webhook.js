/**
 * Optional Serverless Telegram -> GitHub Actions Webhook Relay (Cloudflare Worker, Free Tier)
 * ===========================================================================================
 * Gives 100% cloud-hosted users instant Telegram responses without running a 24/7 home daemon:
 *   1. Deploy on Cloudflare Workers (free) with secrets:
 *        - TELEGRAM_ALLOWED_CHAT_ID
 *        - GITHUB_PAT (repo scope)
 *        - GITHUB_REPO (e.g. "usuario/fantasy-champion")
 *   2. Register Telegram webhook ONLY if NOT using `--listen` locally.
 *      (Note: If using `--listen` or GitHub Actions polling, leave Telegram webhook unset so `getUpdates` drains cleanly,
 *       OR pass the incoming message directly inside `client_payload` below!)
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Fantasy Champion v3 Telegram Relay OK", { status: 200 });
    }
    try {
      const update = await request.json();
      const msg = update.message || update.edited_message || {};
      const chatId = String((msg.chat || {}).id || "");
      if (env.TELEGRAM_ALLOWED_CHAT_ID && chatId !== String(env.TELEGRAM_ALLOWED_CHAT_ID)) {
        return new Response("Unauthorized chat", { status: 200 });
      }
      await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_PAT}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "FantasyChampion-Telegram-Relay",
        },
        body: JSON.stringify({
          event_type: "telegram-message",
          client_payload: {
            chat_id: chatId,
            text: String(msg.text || ""),
            update_id: update.update_id || 0,
          },
        }),
      });
      return new Response("Dispatched", { status: 200 });
    } catch (err) {
      return new Response("Error: " + String(err), { status: 200 });
    }
  },
};
