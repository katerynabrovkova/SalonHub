// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api/client.ts function boundary, not raw fetch -- same seam
// AuthContext.test.tsx already mocks at (AuthProvider's own auth/me/ mount
// fetch, plus logout()'s POST, both go through this).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

// Imported after the mocks above so the mocked modules are what the
// component sees.
import { apiRequest } from "@/lib/api/client";
import { AuthProvider } from "@/app/AuthContext";
import ClientAvatarMenu from "./ClientAvatarMenu";

const mockedApiRequest = vi.mocked(apiRequest);

const ME = { email: "alice@example.com", role: "client" as const, name: "Alice", phone: null };

function mockApiRoutes({ logout }: { logout?: () => unknown } = {}) {
  mockedApiRequest.mockImplementation((...args) => {
    const [, path, options] = args as [string, string, RequestInit | undefined];
    if (path === "/auth/me/") {
      return Promise.resolve(ME);
    }
    if (path === "/auth/logout/" && (options?.method ?? "GET").toUpperCase() === "POST") {
      return logout
        ? Promise.resolve(logout())
        : Promise.reject(new Error("unexpected logout call"));
    }
    throw new Error(`unexpected apiRequest call: ${path}`);
  });
}

function renderMenu() {
  return render(
    <AuthProvider>
      <ClientAvatarMenu />
    </AuthProvider>,
  );
}

beforeEach(() => {
  mockedApiRequest.mockReset();
  replaceMock.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ClientAvatarMenu", () => {
  it("test_avatar_click_opens_the_popover_and_outside_click_closes_it", async () => {
    const user = userEvent.setup();
    mockApiRoutes();

    render(
      <AuthProvider>
        <p>outside content</p>
        <ClientAvatarMenu />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Меню акаунту" })).toBeInTheDocument());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Меню акаунту" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.click(screen.getByText("outside content"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("test_profile_link_navigates_to_client_profile", async () => {
    const user = userEvent.setup();
    mockApiRoutes();

    renderMenu();
    await waitFor(() => expect(screen.getByRole("button", { name: "Меню акаунту" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Меню акаунту" }));

    expect(screen.getByRole("menuitem", { name: "Профіль" })).toHaveAttribute(
      "href",
      "/client/profile",
    );
  });

  it("test_logout_menu_item_confirmed_calls_logout_and_navigates_to_login", async () => {
    const user = userEvent.setup();
    mockApiRoutes({ logout: () => undefined });

    renderMenu();
    await waitFor(() => expect(screen.getByRole("button", { name: "Меню акаунту" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Меню акаунту" }));
    await user.click(screen.getByRole("menuitem", { name: "Вийти" }));

    expect(window.confirm).toHaveBeenCalledWith("Ви впевнені, що хочете вийти?");
    await waitFor(() =>
      expect(mockedApiRequest.mock.calls.some(([, path]) => path === "/auth/logout/")).toBe(true),
    );
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("test_logout_menu_item_declined_does_not_logout_or_navigate", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    mockApiRoutes();

    renderMenu();
    await waitFor(() => expect(screen.getByRole("button", { name: "Меню акаунту" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Меню акаунту" }));
    await user.click(screen.getByRole("menuitem", { name: "Вийти" }));

    expect(window.confirm).toHaveBeenCalledWith("Ви впевнені, що хочете вийти?");
    expect(mockedApiRequest.mock.calls.some(([, path]) => path === "/auth/logout/")).toBe(false);
    expect(replaceMock).not.toHaveBeenCalled();
    // Stayed where they were: menu is still open, user is still shown as
    // signed in (avatar still rendered).
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Меню акаунту" })).toBeInTheDocument();
  });
});
