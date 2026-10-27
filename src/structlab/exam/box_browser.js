// Box 共有ページを Chromium で開き、描画後のページ情報を JSON で標準出力に書く（Node 版 Playwright 用）。
// 使い方: node box_browser.js <url> [--download <dir>] [--timeout <ms>]
// --download は、ページ自身が「ダウンロード可」と示しているときに通常のダウンロード操作だけを行う。
// 再生用の通信を横取りしてファイルを取り出すことはしない。
const { chromium } = require('playwright');

function arg(name, def) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : def;
}

(async () => {
  const url = process.argv[2];
  const timeout = parseInt(arg('--timeout', '45000'), 10);
  const downloadDir = arg('--download', null);
  const out = { ok: false, backend: 'node-playwright', url };
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ acceptDownloads: !!downloadDir });
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
    out.http_status = resp ? resp.status() : null;
    // networkidle は動画プレーヤーの通信で終わらないことがあるため使わない。
    // 本文が描画される（ファイル名・ログイン画面・エラー文のどれかが出る）まで待つ。
    await page.waitForFunction(() => {
      const t = (document.body && document.body.innerText || '').trim();
      return t.length > 40 || document.querySelector('input[type=password]');
    }, null, { timeout }).catch(() => { out.render_timeout = true; });
    // 動画のときはプレーヤーが再生時間を読み込むまで待つ（取れなければ不明のまま）
    if (await page.$('video') || /\.(mp4|mov|m4v|mkv|webm)\b/i.test(await page.title())) {
      await page.waitForFunction(() => {
        const v = document.querySelector('video');
        return v && isFinite(v.duration) && v.duration > 0;
      }, null, { timeout: Math.min(timeout, 20000) }).catch(() => {});
    }
    out.video_duration = await page.evaluate(() => {
      const v = document.querySelector('video');
      return v && isFinite(v.duration) ? v.duration : null;
    });
    out.final_url = page.url();
    out.title = await page.title();
    out.text = await page.evaluate(() => (document.body && document.body.innerText) || '');
    out.html = await page.content();
    out.has_password_input = !!(await page.$('input[type=password]'));
    out.ok = true;
    if (downloadDir) {
      const btn = await page.$('[data-testid=download-preview], button[aria-label="Download"], button:has-text("Download"), button:has-text("ダウンロード")');
      if (!btn) {
        out.download = { ok: false, reason: 'ダウンロードボタンが無い' };
      } else {
        const [dl] = await Promise.all([page.waitForEvent('download', { timeout }), btn.click()]);
        const name = dl.suggestedFilename();
        const path = require('path').join(downloadDir, name);
        await dl.saveAs(path);
        out.download = { ok: true, path, name };
      }
    }
  } catch (e) {
    out.error = String(e && e.message || e).slice(0, 500);
    out.error_kind = /Timeout/i.test(out.error) ? 'timeout'
      : /ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION|ERR_TUNNEL|ERR_PROXY/i.test(out.error) ? 'network'
      : 'browser';
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  process.stdout.write(JSON.stringify(out));
})();
