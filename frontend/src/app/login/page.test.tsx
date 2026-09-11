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

// Imported after the mocks above so the mocked module is what page.tsx sees.
import { apiRequest } from "@/lib/api/client";
import LoginPage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);

function findCall(path: string) {
  return mockedApiRequest.mock.calls.find(([, calledPath]) => calledPath.includes(path));
}

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), "person@example.com");
  await user.type(screen.getByLabelText(/password/i), "correct-horse-battery-staple");
  await user.click(screen.getByRole("button", { name: /log in/i }));
}

beforeEach(() => {
  pushMock.mockReset();
  mockedApiRequest.mockReset();
});

describe("LoginPage", () => {
  test("test_primes_csrf_cookie_on_mount", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);

    render(<LoginPage />);

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
    mockedApiRequest
      .mockResolvedValueOnce(undefined) // csrf priming on mount
      .mockResolvedValueOnce(undefined) // POST auth/login/
      .mockResolvedValueOnce({ email: "person@example.com", role: "admin" }); // GET auth/me/

    render(<LoginPage />);
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(findCall("/auth/me/")).toBeDefined());
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin"));
  });

  test("test_successful_login_client_role_redirects_to_client", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce(undefined) // csrf priming on mount
      .mockResolvedValueOnce(undefined) // POST auth/login/
      .mockResolvedValueOnce({ email: "person@example.com", role: "client" }); // GET auth/me/

    render(<LoginPage />);
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => expect(findCall("/auth/me/")).toBeDefined());
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/client"));
  });

  test("test_invalid_credentials_shows_generic_error", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce(undefined) // csrf priming on mount
      .mockRejectedValueOnce(
        new ApiError(401, "invalid_credentials", "Email or password is incorrect."),
      );

    render(<LoginPage />);
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
    mockedApiRequest
      .mockResolvedValueOnce(undefined) // csrf priming on mount
      .mockRejectedValueOnce(
        new ApiError(429, "throttled", "Too many attempts. Please try again later."),
      );

    render(<LoginPage />);
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByText(/too many attempts|try again later/i)).toBeInTheDocument();
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

    mockedApiRequest
      .mockResolvedValueOnce(undefined) // csrf priming on mount
      .mockReturnValueOnce(pendingLogin); // POST auth/login/ — never resolves during this test

    render(<LoginPage />);
    await waitFor(() => expect(findCall("/auth/csrf/")).toBeDefined());

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /log in/i })).toBeDisabled();
    });

    // Clean up the dangling promise so it doesn't leak into other tests.
    resolveLogin(undefined);
  });
});
