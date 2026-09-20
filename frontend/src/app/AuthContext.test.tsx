// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api/client.ts function boundary, not raw fetch — same seam
// login/page.test.tsx already mocks at (see that file's own comment on why).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mock above so the mocked module is what AuthContext.tsx sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { AuthProvider, useAuth } from "./AuthContext";

const mockedApiRequest = vi.mocked(apiRequest);

function findCall(path: string) {
  return mockedApiRequest.mock.calls.find(([, calledPath]) => calledPath.includes(path));
}

function AuthReadout() {
  const { me, loading } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="email">{me?.email ?? ""}</span>
      <span data-testid="role">{me?.role ?? ""}</span>
    </div>
  );
}

function LogoutButton() {
  const { logout } = useAuth();
  return <button onClick={() => void logout()}>log out</button>;
}

beforeEach(() => {
  mockedApiRequest.mockReset();
});

describe("AuthProvider", () => {
  it("test_fetches_me_on_mount_and_exposes_the_result", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      email: "person@example.com",
      role: "client",
      name: null,
      phone: null,
      email_verified: true,
    });

    render(
      <AuthProvider>
        <AuthReadout />
      </AuthProvider>,
    );

    await waitFor(() => expect(findCall("/auth/me/")).toBeDefined());
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    expect(screen.getByTestId("email")).toHaveTextContent("person@example.com");
    expect(screen.getByTestId("role")).toHaveTextContent("client");
  });

  it("test_a_401_from_me_results_in_signed_out_state_not_a_thrown_error", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
    );

    render(
      <AuthProvider>
        <AuthReadout />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    // No crash: the readout rendered and shows the signed-out shape.
    expect(screen.getByTestId("email")).toHaveTextContent("");
    expect(screen.getByTestId("role")).toHaveTextContent("");
  });

  it("test_login_updates_context_state_visible_to_consumers_without_a_remount", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockRejectedValueOnce(new ApiError(401, "not_authenticated", "Not authenticated.")) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined) // POST /auth/login/
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "admin",
        name: null,
        phone: null,
        email_verified: true,
      }); // login()'s own GET /auth/me/

    function LoginButton() {
      const { login } = useAuth();
      return (
        <button onClick={() => void login("alice@example.com", "correct-horse-battery-staple")}>
          log in
        </button>
      );
    }

    render(
      <AuthProvider>
        <AuthReadout />
        <LoginButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("email")).toHaveTextContent("");

    await user.click(screen.getByRole("button", { name: "log in" }));

    await waitFor(() => {
      expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");
    });
    expect(screen.getByTestId("role")).toHaveTextContent("admin");
  });

  it(
    "test_value_persists_across_a_simulated_rerender_of_children_with_the_same_provider_instance",
    async () => {
      mockedApiRequest.mockResolvedValueOnce({
        email: "person@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      });

      function OtherChild() {
        return <p>a different child than before</p>;
      }

      const { rerender } = render(
        <AuthProvider>
          <AuthReadout />
        </AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
      expect(screen.getByTestId("email")).toHaveTextContent("person@example.com");

      // Re-renders the SAME Provider element (not a fresh <AuthProvider>
      // instance) with different children in its subtree — the shape of a
      // client-side navigation where the root layout's Provider persists
      // but the page underneath it changes.
      act(() => {
        rerender(
          <AuthProvider>
            <OtherChild />
            <AuthReadout />
          </AuthProvider>,
        );
      });

      expect(screen.getByText("a different child than before")).toBeInTheDocument();
      expect(screen.getByTestId("email")).toHaveTextContent("person@example.com");
    },
  );

  it("test_logout_posts_to_the_logout_endpoint", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "admin",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    await user.click(screen.getByRole("button", { name: "log out" }));

    await waitFor(() => expect(findCall("/auth/logout/")).toBeDefined());
    expect(findCall("/auth/logout/")?.[2]).toMatchObject({ method: "POST" });
  });

  it("test_logout_clears_me_on_success_visible_without_a_remount", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "admin",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");

    await user.click(screen.getByRole("button", { name: "log out" }));

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
    expect(screen.getByTestId("role")).toHaveTextContent("");
  });

  it("test_logout_clears_me_even_when_the_logout_call_rejects", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "admin",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockRejectedValueOnce(
        new ApiError(400, "invalid_token", "Invalid or already-used refresh token."),
      ); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");

    await user.click(screen.getByRole("button", { name: "log out" }));

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
    expect(screen.getByTestId("role")).toHaveTextContent("");
  });
});

describe("AuthProvider refresh", () => {
  function RefreshButton() {
    const { refresh } = useAuth();
    return <button onClick={() => void refresh()}>refresh</button>;
  }

  it("test_refresh_replaces_me_with_the_new_auth_me_response", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "client",
        name: "Alice",
        phone: "+10000000000",
        email_verified: true,
      }); // refresh()'s own GET /auth/me/

    function NameReadout() {
      const { me } = useAuth();
      return <span data-testid="name">{me?.name ?? ""}</span>;
    }

    render(
      <AuthProvider>
        <NameReadout />
        <RefreshButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent(""));

    await user.click(screen.getByRole("button", { name: "refresh" }));

    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("Alice"));
  });

  it("test_loading_never_flips_true_during_refresh", async () => {
    const user = userEvent.setup();
    const observedLoadingValues: boolean[] = [];

    function LoadingRecorder() {
      const { loading } = useAuth();
      observedLoadingValues.push(loading);
      return null;
    }

    let resolveRefresh: (value: unknown) => void = () => {};
    const pendingRefresh = new Promise((resolve) => {
      resolveRefresh = resolve;
    });
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockReturnValueOnce(pendingRefresh); // refresh()'s own GET /auth/me/

    render(
      <AuthProvider>
        <LoadingRecorder />
        <RefreshButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(observedLoadingValues.at(-1)).toBe(false));

    // The mount fetch itself legitimately passes through `true` before
    // settling -- only values observed from here on (spanning the refresh
    // call) are what this test is actually about.
    observedLoadingValues.length = 0;

    await user.click(screen.getByRole("button", { name: "refresh" }));

    resolveRefresh({
      email: "alice@example.com",
      role: "client",
      name: "Alice",
      phone: null,
      email_verified: true,
    });
    await waitFor(() => expect(observedLoadingValues.at(-1)).toBe(false));

    expect(observedLoadingValues).not.toContain(true);
  });

  it("test_a_401_during_refresh_sets_me_to_null", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockRejectedValueOnce(
        new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
      ); // refresh()'s own GET /auth/me/

    render(
      <AuthProvider>
        <AuthReadout />
        <RefreshButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "refresh" }));

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
    expect(screen.getByTestId("role")).toHaveTextContent("");
  });

  it("test_a_non_401_failure_during_refresh_keeps_the_previous_me_and_does_not_reject", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce({
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      }) // mount GET /auth/me/
      .mockRejectedValueOnce(new Error("network down")); // refresh()'s own GET /auth/me/

    let refreshResult: "resolved" | "rejected" | null = null;

    function RefreshCapture() {
      const { refresh } = useAuth();
      return (
        <button
          onClick={() =>
            void refresh().then(
              () => {
                refreshResult = "resolved";
              },
              () => {
                refreshResult = "rejected";
              },
            )
          }
        >
          refresh
        </button>
      );
    }

    render(
      <AuthProvider>
        <AuthReadout />
        <RefreshCapture />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "refresh" }));

    await waitFor(() => expect(refreshResult).toBe("resolved"));
    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");
  });
});

describe("useAuth", () => {
  it("test_throws_when_used_outside_the_provider", () => {
    function Lonely() {
      useAuth();
      return null;
    }

    // React logs the thrown error to the console during the failed render;
    // suppress it here so the expected assertion isn't drowned out.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => render(<Lonely />)).toThrow("useAuth must be used within an AuthProvider");

    consoleError.mockRestore();
  });
});
