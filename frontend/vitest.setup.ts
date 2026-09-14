import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

// vitest.config.mts doesn't set `test.globals: true`, so RTL's automatic
// afterEach(cleanup) (which only registers when it finds a global `afterEach`)
// never fires between tests. Register it explicitly instead — without this,
// component test files stack up unmounted DOM across tests in the same file.
afterEach(cleanup);

// jsdom 30 doesn't implement HTMLDialogElement.prototype.showModal/close at
// all (not a "not implemented" warning — the methods are simply undefined,
// so calling them throws a TypeError). Stub them here, test-environment
// only, so components using the real showModal()/close() API (for genuine
// modal semantics in the browser) don't crash under jsdom. Guarded by the
// typeof check because this file also runs for the plain-TypeScript logic
// tests that use vitest's default "node" environment, where
// HTMLDialogElement doesn't exist as a global at all. Never imported by
// production code.
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
}
