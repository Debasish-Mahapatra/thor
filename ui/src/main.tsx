import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { APP_NAME } from "./brand";
import "./styles.css";

document.title = APP_NAME;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
