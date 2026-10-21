// Web components. <nw-fx-converter> uses an OPEN shadow root; <nw-security-key> uses a CLOSED one.
class FxConverter extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>:host{display:block;border:1px solid #cbd5e1;padding:12px;border-radius:8px} label{display:block;margin:4px 0}</style>
      <h3>${this.getAttribute('heading') || 'Currency converter'}</h3>
      <label for="gbp">Amount in GBP</label><input id="gbp" inputmode="decimal">
      <label for="cur">Target currency</label>
      <select id="cur"><option value="EUR">EUR</option><option value="USD">USD</option><option value="CHF">CHF</option></select>
      <button type="button" id="go">Convert</button>
      <p id="out" role="status"></p>`;
    const rates = { EUR: 1.1532, USD: 1.2741, CHF: 1.0123 };
    root.getElementById('go').addEventListener('click', () => {
      const v = Number(root.getElementById('gbp').value.replace(/,/g, ''));
      const c = root.getElementById('cur').value;
      root.getElementById('out').textContent = Number.isFinite(v) && v > 0 ? `${v.toFixed(2)} GBP = ${(v * rates[c]).toFixed(2)} ${c}` : 'Enter a positive amount';
    });
  }
}
customElements.define('nw-fx-converter', FxConverter);

class SecurityKey extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'closed' });
    root.innerHTML = `
      <style>:host{display:block;border:1px dashed #94a3b8;padding:12px;border-radius:8px} code{display:inline-block;margin:2px 6px}</style>
      <p>Recovery codes let you sign in if you lose your authenticator.</p>
      <button type="button">Generate recovery codes</button>
      <div class="codes" aria-live="polite"></div>`;
    root.querySelector('button').addEventListener('click', () => {
      const codes = Array.from({ length: 8 }, (_, i) => `NW-${String(1000 + i * 1371).slice(-4)}-${String(7000 + i * 913).slice(-4)}`);
      root.querySelector('.codes').innerHTML = `<p>8 recovery codes generated</p>` + codes.map((c) => `<code>${c}</code>`).join('');
    });
  }
}
customElements.define('nw-security-key', SecurityKey);
