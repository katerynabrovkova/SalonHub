// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

// ContactInfoForm calls useRouter() itself -- same mock as its own test file.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Imported after the mock above so the mocked module is what BookingStep4.tsx
// sees.
import { BookingContactInfoProvider } from "./BookingContactInfoContext";
import BookingStep4 from "./BookingStep4";

const BASE_PROPS = {
  serviceName: "Manicure",
  specialistName: "Olena",
  slug: "bella-demo",
  entry: "service" as const,
  service: "5",
  specialist: "9",
  startDatetime: "2026-09-24T14:30:00+03:00",
};

function renderStep4(props = BASE_PROPS) {
  return render(
    <BookingContactInfoProvider>
      <BookingStep4 {...props} />
    </BookingContactInfoProvider>,
  );
}

beforeEach(() => {
  mockedUseAuth.mockReset();
});

describe("BookingStep4", () => {
  it("test_renders_nothing_while_auth_is_loading", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: true, login: vi.fn(), logout: vi.fn() });

    const { container } = renderStep4();

    expect(container).toBeEmptyDOMElement();
  });

  it("test_no_session_renders_summary_and_the_guest_form", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });

    renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Olena")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Підтвердити запис" })).toBeInTheDocument();
    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
  });

  it("test_admin_role_renders_the_guest_form", () => {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "admin@example.com",
        role: "admin",
        name: null,
        phone: null,
        email_verified: true,
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderStep4();

    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
  });

  it("test_client_with_unverified_email_renders_the_guest_form", () => {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "alice@example.com",
        role: "client",
        name: null,
        phone: null,
        email_verified: false,
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    });

    renderStep4();

    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
  });
});
