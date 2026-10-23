import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { KitRoot } from "./kit";

class Boundary extends React.Component<{ children: React.ReactNode }, { err: any }> {
  state = { err: null as any };
  static getDerivedStateFromError(err: any) { return { err }; }
  componentDidCatch(err: any) { (window as any).__appError = String(err?.stack || err); }
  render() {
    if (this.state.err) return <div className="kit-page"><div className="kit-error" data-app-crash>This page hit an error: {String(this.state.err?.message || this.state.err)}</div></div>;
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(<KitRoot><Boundary><App /></Boundary></KitRoot>);
