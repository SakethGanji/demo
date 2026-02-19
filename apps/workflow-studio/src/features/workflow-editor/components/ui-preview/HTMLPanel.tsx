import { useUIModeStore } from '../../stores/uiModeStore';
import { Code } from 'lucide-react';

export function HTMLPanel() {
  const htmlContent = useUIModeStore((s) => s.htmlContent);

  // Inject dark-theme defaults into iframe so content isn't black-on-white
  const themedContent = htmlContent
    ? injectIframeTheme(htmlContent)
    : null;

  return (
    <div className="flex flex-col h-full rounded-lg border bg-background">
      <div className="px-4 py-2 border-b flex items-center gap-2">
        <Code className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-medium text-foreground">Preview</h3>
      </div>
      <div className="flex-1 overflow-auto">
        {themedContent ? (
          <iframe
            srcDoc={themedContent}
            title="HTML Preview"
            className="w-full h-full border-0"
            sandbox="allow-scripts"
          />
        ) : (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
            HTML output will appear here...
          </div>
        )}
      </div>
    </div>
  );
}

/** Prepend a <style> block so the iframe inherits a dark-friendly palette. */
function injectIframeTheme(html: string): string {
  const themeCSS = `<style data-theme="injected">
  :root {
    color-scheme: dark;
    --fg: #e5e5e5;
    --bg: #242424;
    --muted: #999;
    --border: #3d3d3d;
    --link: #18a0fb;
  }
  body {
    color: var(--fg);
    background: var(--bg);
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 13px;
    line-height: 1.6;
    margin: 8px;
  }
  a { color: var(--link); }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
  th { background: rgba(255,255,255,0.05); font-weight: 600; }
  pre, code { background: rgba(255,255,255,0.06); border-radius: 4px; padding: 2px 4px; font-size: 0.9em; }
  pre { padding: 12px; overflow-x: auto; }
  hr { border-color: var(--border); }
  img { max-width: 100%; }
</style>`;

  // If the HTML already has a <head>, inject inside it; otherwise prepend
  if (/<head[\s>]/i.test(html)) {
    return html.replace(/<head([\s>])/i, `<head$1${themeCSS}`);
  }
  return themeCSS + html;
}
