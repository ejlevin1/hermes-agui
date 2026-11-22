// MUST be the first import of main.tsx (ESM evaluates imports in order): libraries build zod v4 schemas at module load,
// and zod v4 probes `new Function("")` (=> CSP `script-src` eval violation) unless jitless is already set.
import { config } from "zod/v4/core";
config({ jitless: true });
