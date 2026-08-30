from playwright.sync_api import sync_playwright

from src.config import ROOT, Settings


def main() -> None:
    settings = Settings.load()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        js = page.request.get("https://cdn.vefrop.com/mobile/wallet/admin.js?v=9960").text()
        keys = ("passcode2fa", "login", "captcha", "2fa", "btn.login")
        chunks = []
        for key in keys:
            pos = 0
            found = 0
            while found < 3:
                idx = js.lower().find(key.lower(), pos)
                if idx < 0:
                    break
                chunks.append(f"\n--- {key} @{idx} ---\n" + js[max(0, idx - 180) : idx + 350])
                pos = idx + 1
                found += 1
        page.goto(settings.dashboard_url.replace("#transactions", "#login"), wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.fill('input[name="username"]', settings.dashboard_username)
        page.fill('input[name="password"]', settings.dashboard_password)
        page.evaluate(
            """(code) => {
              const el = document.querySelector('input[name=passcode2fa]');
              if (el) { el.style.display='block'; el.value=code; }
              document.querySelector('a.btn.login')?.click();
            }""",
            settings.dashboard_2fa,
        )
        page.wait_for_timeout(2500)
        swal = page.evaluate(
            """() => ({
              title: document.querySelector('.swal2-title')?.innerText || '',
              content: document.querySelector('.swal2-html-container, .swal2-content')?.innerText || '',
              captcha: !!document.querySelector('[id*=captcha], .aliyun, #aliyunCaptcha, iframe[src*="captcha"]'),
            })"""
        )
        (ROOT / "data" / "login_diag.txt").write_text(
            "swal=" + repr(swal) + "\n" + "".join(chunks)[:12000],
            encoding="utf-8",
        )
        print("done")
        browser.close()


if __name__ == "__main__":
    main()
