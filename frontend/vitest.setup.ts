import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

// vitest.config.mts doesn't set `test.globals: true`, so RTL's automatic
// afterEach(cleanup) (which only registers when it finds a global `afterEach`)
// never fires between tests. Register it explicitly instead — without this,
// component test files stack up unmounted DOM across tests in the same file.
afterEach(cleanup);
