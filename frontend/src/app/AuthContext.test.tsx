// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api/client.ts function boundary, not raw fetch — same seam
// login/page.test.tsx already mocks at (see that file's own comment on why).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// S2c (docs/DECISIONS.md, "S2 design details", point 10): AuthContext is
// expected to call `whenRenewalIdle` from logout(). Mocked here, same as
// apiRequest, since AuthContext.tsx doesn't import it yet (red phase).
vi.mock("@/lib/api/renewSession", () => ({
  whenRenewalIdle: vi.fn(),
  renewSession: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what AuthContext.tsx sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import { whenRenewalIdle } from "@/lib/api/renewSession";
import { SESSION_EXPIRED_EVENT } from "@/lib/api/sessionHint";
import { AuthProvider, useAuth } from "./AuthContext";

const mockedApiRequest = vi.mocked(apiRequest);
const mockedWhenRenewalIdle = vi.mocked(whenRenewalIdle);

const ALICE = {
  email: "alice@example.com",
  role: "client",
  name: null,
  phone: null,
  email_verified: true,
};

function clearSessionHintCookie() {
  document.cookie = "session_hint=; max-age=0; path=/";
}

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
  mockedWhenRenewalIdle.mockReset();
});

afterEach(() => {
  clearSessionHintCookie();
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

  it("test_mount_401_still_sets_me_null_and_loading_false", async () => {
    // docs/DECISIONS.md, "S2 design details" -- guards that today's mount
    // behavior stays correct once the S2c renewal/logout changes land.
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
    );

    render(
      <AuthProvider>
        <AuthReadout />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("email")).toHaveTextContent("");
    expect(screen.getByTestId("role")).toHaveTextContent("");
  });

  it("test_session_expired_event_sets_me_to_null_regardless_of_in_flight_apirequest_activity", async () => {
    mockedApiRequest.mockResolvedValueOnce(ALICE);

    render(
      <AuthProvider>
        <AuthReadout />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    act(() => {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    });

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
    expect(screen.getByTestId("role")).toHaveTextContent("");
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

  it("test_logout_deletes_session_hint_synchronously_before_the_logout_post_resolves", async () => {
    const user = userEvent.setup();
    document.cookie = "session_hint=1; path=/";
    let resolveLogoutPost: (value: unknown) => void = () => {};
    const pendingLogout = new Promise((resolve) => {
      resolveLogoutPost = resolve;
    });
    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockReturnValueOnce(pendingLogout); // POST /auth/logout/, held open

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "log out" }));

    // The logout POST is still pending, but the hint cookie must already be gone.
    await waitFor(() => expect(document.cookie).not.toContain("session_hint"));
    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");

    resolveLogoutPost(undefined);
    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
  });

  it("test_logout_waits_for_an_in_flight_renewal_before_sending_the_logout_post", async () => {
    const user = userEvent.setup();
    let resolveIdle: () => void = () => {};
    const idlePromise = new Promise<void>((resolve) => {
      resolveIdle = resolve;
    });
    mockedWhenRenewalIdle.mockReturnValueOnce(idlePromise);

    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "log out" }));

    // The renewal is still in flight: the logout POST must not have gone out yet.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(findCall("/auth/logout/")).toBeUndefined();

    resolveIdle();

    await waitFor(() => expect(findCall("/auth/logout/")).toBeDefined());
  });

  it("test_logout_deletes_session_hint_before_whenrenewalidle_even_resolves", async () => {
    // Isolates the FIRST deleteSessionHint() call specifically. The second
    // call (after whenRenewalIdle resolves, before the POST) would also
    // make the cookie gone by POST time regardless of where the first call
    // sits, so that alone can't tell the two apart -- this test holds
    // whenRenewalIdle open and checks the cookie while it is still pending,
    // a point only the first call can have reached.
    const user = userEvent.setup();
    document.cookie = "session_hint=1; path=/";
    let resolveIdle: () => void = () => {};
    const idlePromise = new Promise<void>((resolve) => {
      resolveIdle = resolve;
    });
    mockedWhenRenewalIdle.mockReturnValueOnce(idlePromise);

    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "log out" }));

    // whenRenewalIdle is still pending -- the hint cookie must already be gone.
    await waitFor(() => expect(document.cookie).not.toContain("session_hint"));
    expect(findCall("/auth/logout/")).toBeUndefined();

    resolveIdle();

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
  });

  it("test_logout_deletes_session_hint_again_after_an_in_flight_renewal_resolves_renewed", async () => {
    const user = userEvent.setup();
    mockedWhenRenewalIdle.mockImplementationOnce(async () => {
      // Simulates the in-flight renewal completing as "renewed" and
      // re-setting the hint cookie right before whenRenewalIdle resolves.
      document.cookie = "session_hint=1; path=/";
    });

    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockResolvedValueOnce(undefined); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "log out" }));

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent(""));
    expect(document.cookie).not.toContain("session_hint");
  });

  it("test_logout_still_swallows_a_rejected_logout_post_and_clears_me", async () => {
    // Same behavior as test_logout_clears_me_even_when_the_logout_call_rejects
    // above; kept as its own test because S2c adds new work (session_hint
    // deletion, whenRenewalIdle) ahead of the POST that could plausibly
    // reintroduce a thrown error here.
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockRejectedValueOnce(
        new ApiError(400, "invalid_token", "Invalid or already-used refresh token."),
      ); // POST /auth/logout/

    render(
      <AuthProvider>
        <AuthReadout />
        <LogoutButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

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

  it("test_a_renewalunsureerror_during_refresh_leaves_me_unchanged", async () => {
    const user = userEvent.setup();
    mockedApiRequest
      .mockResolvedValueOnce(ALICE) // mount GET /auth/me/
      .mockRejectedValueOnce(new RenewalUnsureError()); // refresh()'s own GET /auth/me/

    render(
      <AuthProvider>
        <AuthReadout />
        <RefreshButton />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com"));

    await user.click(screen.getByRole("button", { name: "refresh" }));

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");
    expect(screen.getByTestId("role")).toHaveTextContent("client");
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
