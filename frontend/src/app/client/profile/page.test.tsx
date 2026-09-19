// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api/client.ts function boundary, not raw fetch -- same seam
// AuthContext.test.tsx/client/page.test.tsx already mock at.
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

// Imported after the mocks above so the mocked modules are what the page sees.
import { apiRequest } from "@/lib/api/client";
import { AuthProvider } from "@/app/AuthContext";
import ClientProfilePage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);

function mockApiRoutes({
  me,
  logout,
}: {
  me: unknown;
  logout?: () => unknown;
}) {
  mockedApiRequest.mockImplementation((...args) => {
    const [, path, options] = args as [string, string, RequestInit | undefined];
    if (path === "/auth/me/") {
      return Promise.resolve(me);
    }
    if (path === "/auth/logout/" && (options?.method ?? "GET").toUpperCase() === "POST") {
      return logout
        ? Promise.resolve(logout())
        : Promise.reject(new Error("unexpected logout call"));
    }
    throw new Error(`unexpected apiRequest call: ${path}`);
  });
}

function renderProfile() {
  return render(
    <AuthProvider>
      <ClientProfilePage />
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

describe("ClientProfilePage", () => {
  it("test_all_four_rows_render_with_a_linked_customer", async () => {
    mockApiRoutes({
      me: { email: "alice@example.com", role: "client", name: "Alice", phone: "+10000000000" },
    });

    renderProfile();

    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeInTheDocument());
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("Ім'я")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Телефон")).toBeInTheDocument();
    expect(screen.getByText("+10000000000")).toBeInTheDocument();
    expect(screen.getByText("Пароль")).toBeInTheDocument();
  });

  it("test_only_email_and_password_rows_render_with_no_linked_customer", async () => {
    mockApiRoutes({
      me: { email: "unlinked@example.com", role: "client", name: null, phone: null },
    });

    renderProfile();

    await waitFor(() => expect(screen.getByText("unlinked@example.com")).toBeInTheDocument());
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("Пароль")).toBeInTheDocument();
    // Absent, not shown as an empty/placeholder row.
    expect(screen.queryByText("Ім'я")).not.toBeInTheDocument();
    expect(screen.queryByText("Телефон")).not.toBeInTheDocument();
  });

  it("test_row_links_point_to_their_own_subpages", async () => {
    mockApiRoutes({
      me: { email: "alice@example.com", role: "client", name: "Alice", phone: "+10000000000" },
    });

    renderProfile();

    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeInTheDocument());
    expect(screen.getByText("alice@example.com").closest("a")).toHaveAttribute(
      "href",
      "/client/profile/email",
    );
    expect(screen.getByText("Alice").closest("a")).toHaveAttribute(
      "href",
      "/client/profile/name",
    );
    expect(screen.getByText("+10000000000").closest("a")).toHaveAttribute(
      "href",
      "/client/profile/phone",
    );
    expect(screen.getByText("Пароль").closest("a")).toHaveAttribute(
      "href",
      "/client/profile/password",
    );
  });

  it("test_back_link_points_to_client_dashboard", async () => {
    mockApiRoutes({
      me: { email: "alice@example.com", role: "client", name: null, phone: null },
    });

    renderProfile();

    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Мої записи/ })).toHaveAttribute("href", "/client");
  });

  it("test_logout_button_confirmed_calls_logout_and_navigates_to_login", async () => {
    const user = userEvent.setup();
    mockApiRoutes({
      me: { email: "alice@example.com", role: "client", name: null, phone: null },
      logout: () => undefined,
    });

    renderProfile();
    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Вийти" }));

    expect(window.confirm).toHaveBeenCalledWith("Ви впевнені, що хочете вийти?");
    await waitFor(() =>
      expect(mockedApiRequest.mock.calls.some(([, path]) => path === "/auth/logout/")).toBe(true),
    );
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("test_logout_button_declined_does_not_logout_or_navigate", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    mockApiRoutes({
      me: { email: "alice@example.com", role: "client", name: null, phone: null },
    });

    renderProfile();
    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Вийти" }));

    expect(window.confirm).toHaveBeenCalledWith("Ви впевнені, що хочете вийти?");
    expect(mockedApiRequest.mock.calls.some(([, path]) => path === "/auth/logout/")).toBe(false);
    expect(replaceMock).not.toHaveBeenCalled();
    // Stayed on the profile page.
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
  });
});
