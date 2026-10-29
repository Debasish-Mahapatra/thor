import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { APP_NAME } from "./brand";
import { installTitlebarBehaviour } from "./native";
import "./styles.css";

document.title = APP_NAME;
installTitlebarBehaviour();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
