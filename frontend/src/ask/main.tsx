// The public page: what visitors see on the public listener (port 8001), and /ask.html as a preview.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { AskPage } from "./AskPage";
import "../styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Markdown renders profile links with <Link>, which needs a router. */}
    <MemoryRouter>
      <AskPage />
    </MemoryRouter>
  </StrictMode>,
);
