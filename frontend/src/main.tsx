import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { AdvisorPage } from "./pages/AdvisorPage";
import { ConsumePage } from "./pages/ConsumePage";
import { PeoplePage } from "./pages/PeoplePage";
import { PersonPage } from "./pages/PersonPage";
import { AppProvider } from "./state";
import "./styles.css";

function NotFound() {
  return (
    <div className="page narrow empty-state">
      <div className="enso" aria-hidden="true" />
      <h2>Nothing here</h2>
      <p>
        <Link to="/">Back to your circle</Link>
      </p>
    </div>
  );
}

function App() {
  return (
    <AppProvider>
      <div className="app">
        <Sidebar />
        <main className="main">
          <Routes>
            <Route path="/" element={<PeoplePage />} />
            <Route path="/people/:slug" element={<PersonPage />} />
            <Route path="/consume" element={<ConsumePage />} />
            <Route path="/advisor" element={<AdvisorPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </AppProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
