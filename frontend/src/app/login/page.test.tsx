// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

// Mock the api/client.ts function boundary, not raw fetch — there's no
// existing component-test precedent in this repo yet, and this matches
// CLAUDE.md's rule that the frontend API client is the one seam that talks
// to `fetch`/cookies (see client.ts's own module docstring).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

// Imported after the mocks above so the mocked module is what page.tsx and
// AuthContext.tsx see.
import { apiRequest } from "@/lib/api/client";
import { AuthProvider } from "@/app/AuthContext";
import LoginPage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);

function findCall(path: string) {
  return mockedApiRequest.mock.calls.find(([, calledPath]) => calledPath.includes(path));
}

function renderLoginPage() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>,
  );
}

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), "person@example.com");
  await user.type(screen.getByLabelText(/password/i), "correct-horse-battery-staple");
  await user.click(screen.getByRole("button", { name: /log in/i }));
}

/**
 * AuthProvider (root layout, docs/DECISIONS.md § Stage 15 planning, item 2)
 * now issues its own `GET auth/me/` on mount, independently of
 * LoginPage's `GET auth/csrf/` mount effect — the two effects' relative
 * firing order is a React/JSDOM implementation detail, not part of this
 * page's contract. Routing every mocked apiRequest call by its `path`
 * argument (rather than a positional `mockResolvedValueOnce` queue) keeps
 * these tests correct regardless of that order, and matches what a real
 * backend would do (respond based on which endpoint was hit).
 */
function mockApiRoutes({
  meBeforeLogin,
  login,
  meAfterLogin,
  csrf,
}: {
  meBeforeLogin?: () => Promise<unknown>;
  login?: () => Promise<unknown>;
  meAfterLogin?: () => Promise<unknown>;
  // Receives the 0-based count of csrf calls so far: 0 is the mount priming.
  csrf?: (callIndex: number) => Promise<unknown>;
} = {}) {
  let loginCalled = false;
  let csrfCalls = 0;
  mockedApiRequest.mockImplementation((...args) => {
    const [, path, options] = args as [string, string, RequestInit | undefined];
    if (path.includes("/auth/csrf/")) {
      const callIndex = csrfCalls++;
      return csrf ? csrf(callIndex) : Promise.resolve(undefined);
    }
    if (path.includes("/auth/login/") && (options?.method ?? "GET").toUpperCase() === "POST") {
      loginCalled = true;
      return login ? login() : Promise.resolve(undefined);
    }
    if (path.includes("/auth/me/")) {
      return loginCalled && meAfterLogin
        ? meAfterLogin()
        : meBeforeLogin
          ? meBeforeLogin()
          : Promise.reject(new ApiError(401, "not_authenticated", "Not authenticated."));
    }
    throw new Error(`unexpected apiRequest call: ${path}`);
  });
}

beforeEach(() => {
  pushMock.mockReset();
  mockedApiRequest.mockReset();
});

afterEach(() => {
  sessionStorage.clear();
  document.cookie = "csrftoken=; max-age=0; path=/";
});

const RETURN_PATH_KEY = "salonhub:return-path";

function meWithRole(role: "admin" | "client") {
  return () =>
    Promise.resolve({
      email: "person@example.com",
      role,
      name: null,
      phone: null,
      email_verified: true,
    });
}

function csrfCallCount() {
  return mockedApiRequest.mock.calls.filter(([, path]) => path.includes("/auth/csrf/")).length;
}

function loginPostCalled() {
  return mockedApiRequest.mock.calls.some(
    ([, path, options]) =>
      path.includes("/auth/login/") && (options?.method ?? "GET").toUpperCase() === "POST",
  );
}

describe("LoginPage", () => {
  test("test_primes_csrf_cookie_on_mount", async () => {
    mockApiRoutes();

    renderLoginPage();

    await waitFor(() => {
      const csrfCall = findCall("/auth/csrf/");
      expect(csrfCall).toBeDefined();
    });

    // No interaction has happened yet — the csrf GET must have fired purely
    // from mounting.
    const csrfCall = findCall("/auth/csrf/");
    const method = (csrfCall?.[2]?.method ?? "GET").toUpperCase();
    expect(method).toBe("GET");
  });

  test("test_successful_login_redirects_by_role", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      login: () => Promise.resolve(undefined),
      meAfterLogin: () =>
        Promise.resolve({
          email: "person@example.com",
          role: "admin",
          name: null,
          phone: null,
          email_verified: true,
        }),
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin"));
  });

  test("test_successful_login_client_role_redirects_to_client", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      login: () => Promise.resolve(undefined),
      meAfterLogin: () =>
        Promise.resolve({
          email: "person@example.com",
          role: "client",
          name: null,
          phone: null,
          email_verified: true,
        }),
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/client"));
  });

  test("test_invalid_credentials_shows_generic_error", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      login: () =>
        Promise.reject(
          new ApiError(401, "invalid_credentials", "Email or password is incorrect."),
        ),
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByText(/email or password is incorrect/i)).toBeInTheDocument();
    });

    // The backend collapses "no such email" and "wrong password" into one
    // response — the UI must not re-introduce that distinction.
    expect(screen.queryByText(/no account (?:found|exists) with that email/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/email not found/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/wrong password/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/incorrect password/i)).not.toBeInTheDocument();
  });

  test("test_rate_limited_shows_throttle_message", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      login: () =>
        Promise.reject(new ApiError(429, "throttled", "Too many attempts. Please try again later.")),
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByText(/забагато спроб|спробуйте пізніше/i)).toBeInTheDocument();
    });

    // Must be a distinct message from the 401 case, not the same generic text.
    expect(screen.queryByText(/email or password is incorrect/i)).not.toBeInTheDocument();
  });

  test("test_submit_disabled_while_pending", async () => {
    const user = userEvent.setup();
    let resolveLogin: (value: unknown) => void = () => {};
    const pendingLogin = new Promise((resolve) => {
      resolveLogin = resolve;
    });

    mockApiRoutes({
      login: () => pendingLogin, // never resolves during this test
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /log in/i })).toBeDisabled();
    });

    // Clean up the dangling promise so it doesn't leak into other tests.
    resolveLogin(undefined);
  });

  test("test_renders_register_link", async () => {
    mockApiRoutes();

    renderLoginPage();

    const registerLink = screen.getByRole("link", { name: /зареєструватися/i });
    expect(registerLink).toHaveAttribute("href", "/register");
  });
});

describe("LoginPage return path", () => {
  // docs/DECISIONS.md, "S3 design details", item 6. "Nothing stored" is
  // covered by the role-based redirect tests above.

  test("test_client_login_goes_to_the_stored_client_path", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(RETURN_PATH_KEY, "/client/profile?tab=x");
    mockApiRoutes({ meAfterLogin: meWithRole("client") });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalled());
    expect(pushMock).toHaveBeenCalledWith("/client/profile?tab=x");
    expect(pushMock).not.toHaveBeenCalledWith("/client");
  });

  test("test_client_login_with_a_foreign_origin_path_stored_goes_to_client", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(RETURN_PATH_KEY, "https://evil.com/client");
    mockApiRoutes({ meAfterLogin: meWithRole("client") });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalled());
    expect(pushMock).toHaveBeenCalledWith("/client");
    expect(pushMock).not.toHaveBeenCalledWith("https://evil.com/client");
  });

  test("test_admin_login_with_a_client_path_stored_goes_to_admin", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(RETURN_PATH_KEY, "/client/profile");
    mockApiRoutes({ meAfterLogin: meWithRole("admin") });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalled());
    expect(pushMock).toHaveBeenCalledWith("/admin");
    expect(pushMock).not.toHaveBeenCalledWith("/client/profile");
  });

  test.each([
    { stored: "/client/profile?tab=x", role: "client" as const },
    { stored: "https://evil.com/client", role: "client" as const },
    { stored: "/client/profile", role: "admin" as const },
  ])(
    "test_successful_login_removes_the_stored_path_($stored,_$role)",
    async ({ stored, role }) => {
      const user = userEvent.setup();
      sessionStorage.setItem(RETURN_PATH_KEY, stored);
      mockApiRoutes({ meAfterLogin: meWithRole(role) });

      renderLoginPage();
      await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

      await fillAndSubmit(user);

      await waitFor(() => expect(pushMock).toHaveBeenCalled());
      expect(sessionStorage.getItem(RETURN_PATH_KEY)).toBeNull();
    },
  );

  test("test_failed_login_leaves_the_stored_path_in_place", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(RETURN_PATH_KEY, "/client/profile");
    mockApiRoutes({
      login: () =>
        Promise.reject(new ApiError(401, "invalid_credentials", "Email or password is incorrect.")),
    });

    renderLoginPage();
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByText(/email or password is incorrect/i)).toBeInTheDocument();
    });
    expect(sessionStorage.getItem(RETURN_PATH_KEY)).toBe("/client/profile");
  });
});

describe("LoginPage csrf priming", () => {
  // docs/DECISIONS.md, "S3 design details", item 7.

  test("test_a_rejecting_csrf_priming_on_mount_causes_no_unhandled_rejection", async () => {
    // vi.fn() attaches its own rejection handler to any returned value that
    // is `instanceof Promise` (to record mock.settledResults), which would
    // hide a missing .catch in the page. A promise from another V8 context
    // fails that check but is still a real, trackable promise.
    const foreignRejection = () =>
      runInNewContext("Promise.reject(new Error('network down'))") as Promise<unknown>;
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    try {
      mockApiRoutes({ csrf: foreignRejection });

      renderLoginPage();
      await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());
      // Node reports unhandled rejections only after the microtask queue
      // drains; two macrotask turns give it the chance to.
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", unhandled);
    }
  });

  test("test_submit_without_csrftoken_cookie_awaits_csrf_before_the_login_post", async () => {
    const user = userEvent.setup();
    let resolveSubmitCsrf: (value: unknown) => void = () => {};
    const pendingSubmitCsrf = new Promise((resolve) => {
      resolveSubmitCsrf = resolve;
    });
    mockApiRoutes({
      meAfterLogin: meWithRole("client"),
      csrf: (callIndex) => (callIndex === 0 ? Promise.resolve(undefined) : pendingSubmitCsrf),
    });

    renderLoginPage();
    await waitFor(() => expect(csrfCallCount()).toBe(1));

    await fillAndSubmit(user);

    // The submit-time csrf call is held open: the login POST must wait for it.
    await waitFor(() => expect(csrfCallCount()).toBe(2));
    expect(loginPostCalled()).toBe(false);

    resolveSubmitCsrf(undefined);

    await waitFor(() => expect(loginPostCalled()).toBe(true));
    const calls = mockedApiRequest.mock.calls.map(([, path]) => path);
    const submitCsrfIndex = calls.findLastIndex((path) => path.includes("/auth/csrf/"));
    const loginIndex = calls.findIndex((path) => path.includes("/auth/login/"));
    expect(submitCsrfIndex).toBeLessThan(loginIndex);
  });

  test("test_submit_with_csrftoken_cookie_makes_no_extra_csrf_call", async () => {
    const user = userEvent.setup();
    document.cookie = "csrftoken=abc; path=/";
    mockApiRoutes({ meAfterLogin: meWithRole("client") });

    renderLoginPage();
    await waitFor(() => expect(csrfCallCount()).toBe(1));

    await fillAndSubmit(user);

    await waitFor(() => expect(pushMock).toHaveBeenCalled());
    expect(loginPostCalled()).toBe(true);
    expect(csrfCallCount()).toBe(1);
  });

  test("test_submit_without_csrftoken_cookie_and_failing_csrf_shows_generic_error_and_sends_no_login", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      meAfterLogin: meWithRole("client"),
      csrf: (callIndex) =>
        callIndex === 0 ? Promise.resolve(undefined) : Promise.reject(new Error("network down")),
    });

    renderLoginPage();
    await waitFor(() => expect(csrfCallCount()).toBe(1));

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByText("Something went wrong. Please try again.")).toBeInTheDocument();
    });
    expect(loginPostCalled()).toBe(false);
    expect(pushMock).not.toHaveBeenCalled();
  });
});
