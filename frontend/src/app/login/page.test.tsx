// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

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
}: {
  meBeforeLogin?: () => Promise<unknown>;
  login?: () => Promise<unknown>;
  meAfterLogin?: () => Promise<unknown>;
} = {}) {
  let loginCalled = false;
  mockedApiRequest.mockImplementation((...args) => {
    const [, path, options] = args as [string, string, RequestInit | undefined];
    if (path.includes("/auth/csrf/")) {
      return Promise.resolve(undefined);
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
