import "./zodConfig"; // MUST stay the first import (zod v4 jitless before any library builds a schema)
import { createRoot } from "react-dom/client";
import { CssBaseline } from "@mui/material";
import App from "./App";
createRoot(document.getElementById("root")!).render(<><CssBaseline /><App /></>);
