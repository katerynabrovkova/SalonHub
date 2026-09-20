// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

// Imported after the mocks above so the mocked modules are what layout.tsx sees.
import ClientLayout from "./layout";

function Children() {
  return <p>protected content</p>;
}

beforeEach(() => {
  replaceMock.mockReset();
  mockedUseAuth.mockReset();
});

describe("ClientLayout", () => {
  it("test_redirects_to_login_when_signed_out", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });

    render(
      <ClientLayout>
        <Children />
      </ClientLayout>,
    );

    expect(replaceMock).toHaveBeenCalledWith("/login");
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("test_renders_children_when_signed_in", () => {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: true,
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    render(
      <ClientLayout>
        <Children />
      </ClientLayout>,
    );

    expect(screen.getByText("protected content")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("test_renders_no_children_and_does_not_redirect_while_loading", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: true, login: vi.fn(), logout: vi.fn() });

    const { container } = render(
      <ClientLayout>
        <Children />
      </ClientLayout>,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("test_redirect_only_fires_after_loading_settles_to_signed_out", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: true, login: vi.fn(), logout: vi.fn() });

    const { rerender } = render(
      <ClientLayout>
        <Children />
      </ClientLayout>,
    );

    expect(replaceMock).not.toHaveBeenCalled();

    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });
    rerender(
      <ClientLayout>
        <Children />
      </ClientLayout>,
    );

    expect(replaceMock).toHaveBeenCalledWith("/login");
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });
});
