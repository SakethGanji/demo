import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import { App } from "./App";
import { IdentityProvider } from "./app/identity";
import { ToastProvider } from "./components/ui";

// Apply persisted theme before first paint.
const savedTheme = localStorage.getItem("analytics.theme");
if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <IdentityProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </IdentityProvider>
    </BrowserRouter>
  </StrictMode>,
);
