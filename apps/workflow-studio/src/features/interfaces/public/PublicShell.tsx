/** Chrome-free frame for the public pages: no studio shell, its own scroll container (the root clips overflow). */
export function PublicShell({ title, description, children }: { title?: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="h-full w-full overflow-y-auto" data-testid="iface-page">
      <div className="mx-auto flex w-full max-w-[560px] flex-col gap-5 px-4 py-10">
        {title && (
          <header className="flex flex-col gap-1">
            <h1 className="text-title leading-[1.1] font-[650] tracking-[-0.03em]" data-testid="iface-title">{title}</h1>
            {description && <p className="text-[13px] text-muted-foreground">{description}</p>}
          </header>
        )}
        {children}
      </div>
    </div>
  )
}

export function PublicNotFound() {
  return (
    <PublicShell>
      <div className="tile flex flex-col gap-1 p-6 text-center" data-testid="iface-not-found">
        <div className="text-[14px] font-semibold">This page isn't available</div>
        <div className="text-[12.5px] text-muted-foreground">The link may be wrong, or the form is not published.</div>
      </div>
    </PublicShell>
  )
}
