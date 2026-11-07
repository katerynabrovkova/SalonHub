// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { renderToString } from "react-dom/server";
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

function renderStep4(props: ComponentProps<typeof BookingStep4> = BASE_PROPS) {
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
  it("test_no_session_renders_summary_and_the_guest_form", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });

    renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Olena")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Підтвердити запис" })).toBeInTheDocument();
    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
    expect(
      screen.queryByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).not.toBeInTheDocument();
    const emailInput = screen.getByLabelText("Email");
    expect(emailInput).toBeInTheDocument();
    expect(emailInput).not.toHaveAttribute("readonly");
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
    expect(
      screen.queryByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).not.toBeInTheDocument();
    const emailInput = screen.getByLabelText("Email");
    expect(emailInput).toBeInTheDocument();
    expect(emailInput).not.toHaveAttribute("readonly");
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
    expect(
      screen.queryByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).not.toBeInTheDocument();
    const emailInput = screen.getByLabelText("Email");
    expect(emailInput).toBeInTheDocument();
    expect(emailInput).not.toHaveAttribute("readonly");
  });

  it("test_verified_client_with_linked_customer_renders_account_form_without_name_and_phone_inputs", () => {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "alice@example.com",
        role: "client",
        name: "Alice",
        phone: "+10000000000",
        email_verified: true,
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    });

    renderStep4();

    expect(
      screen.getByText("Запис на ім'я вашого облікового запису: alice@example.com"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Ім'я")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Телефон")).not.toBeInTheDocument();
  });

  it("test_verified_client_without_linked_customer_renders_account_form_with_name_and_phone_inputs", () => {
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
      refresh: vi.fn(),
    });

    renderStep4();

    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
    expect(screen.getByLabelText("Телефон")).toBeInTheDocument();
    const emailInput = screen.getByLabelText("Email");
    expect(emailInput).toHaveValue("alice@example.com");
    expect(emailInput).toHaveAttribute("readonly");
    expect(
      screen.getByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).toBeInTheDocument();
  });
});

describe("BookingStep4 verification line (item 13, point 7)", () => {
  const VERIFY_LINE = "Підтвердіть пошту, щоб бронювати з акаунта.";

  function mockClient(emailVerified: boolean) {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "alice@example.com",
        role: "client",
        name: "Alice",
        phone: "+10000000000",
        email_verified: emailVerified,
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    });
  }

  it("test_unverified_client_sees_the_verify_line_above_the_guest_form", () => {
    mockClient(false);

    renderStep4();

    const line = screen.getByText(VERIFY_LINE);
    const guestNameInput = screen.getByLabelText("Ім'я");
    expect(line.compareDocumentPosition(guestNameInput) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("readonly");
  });

  it("test_verified_client_sees_no_verify_line", () => {
    mockClient(true);

    renderStep4();

    expect(
      screen.getByText("Запис на ім'я вашого облікового запису: alice@example.com"),
    ).toBeInTheDocument();
    expect(screen.queryByText(VERIFY_LINE)).not.toBeInTheDocument();
  });

  // The line is for clients only.
  it("test_unverified_admin_sees_no_verify_line", () => {
    mockedUseAuth.mockReturnValue({
      me: {
        email: "admin@example.com",
        role: "admin",
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
    expect(screen.queryByText(VERIFY_LINE)).not.toBeInTheDocument();
  });

  it("test_visitor_not_logged_in_sees_no_verify_line", () => {
    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });

    renderStep4();

    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
    expect(screen.queryByText(VERIFY_LINE)).not.toBeInTheDocument();
  });
});

// 17.7f (docs/DECISIONS.md § "Stage 17 sub-step order"): while auth is
// loading, step 4 shows the summary at once and a skeleton in place of the
// form. The negative checks below first confirm the loading state rendered
// (the summary is there), so they cannot pass against an empty page.
describe("BookingStep4 while auth is loading (17.7f)", () => {
  // "24 вересня, 14:30" from BASE_PROPS.startDatetime; \s+ because Intl may
  // use a non-breaking space.
  const SLOT_TEXT = /24\s+вересня,\s+14:30/;
  const LOADING_TEXT = "Завантаження…";

  function mockLoading(me: object | null = null) {
    mockedUseAuth.mockReturnValue({
      me,
      loading: true,
      login: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    });
  }

  function clientMe(emailVerified: boolean) {
    return {
      email: "alice@example.com",
      role: "client",
      name: "Alice",
      phone: "+10000000000",
      email_verified: emailVerified,
    };
  }

  it("test_loading_renders_the_summary", () => {
    mockLoading();

    renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Olena")).toBeInTheDocument();
    expect(screen.getByText(SLOT_TEXT)).toBeInTheDocument();
  });

  it("test_loading_renders_any_specialist_in_the_summary", () => {
    mockLoading();

    renderStep4({ ...BASE_PROPS, specialist: "any", specialistName: null });

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Будь-який спеціаліст")).toBeInTheDocument();
    expect(screen.getByText(SLOT_TEXT)).toBeInTheDocument();
  });

  it("test_loading_renders_no_form_fields_and_no_confirm_button", () => {
    mockLoading();

    renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.queryByLabelText("Ім'я")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Телефон")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Підтвердити запис" })).not.toBeInTheDocument();
  });

  // The component must wait for `loading`, not only for `me`.
  it("test_loading_with_a_known_client_shows_no_account_email", () => {
    mockLoading(clientMe(true));

    const { container } = renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("alice@example.com");
    expect(screen.queryByDisplayValue("alice@example.com")).not.toBeInTheDocument();
  });

  it("test_loading_with_an_unverified_client_shows_no_verify_line", () => {
    mockLoading(clientMe(false));

    renderStep4();

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.queryByText("Підтвердіть пошту, щоб бронювати з акаунта.")).not.toBeInTheDocument();
  });

  it("test_loading_shows_a_visually_hidden_status_line", () => {
    mockLoading();

    renderStep4();

    const text = screen.getByText(LOADING_TEXT);
    expect(text).toHaveClass("sr-only");
    expect(text.closest('[role="status"]')).not.toBeNull();
    // getByRole skips aria-hidden subtrees, so this also proves the status
    // line is not inside the hidden skeleton.
    expect(screen.getByRole("status")).toHaveTextContent(LOADING_TEXT);
  });

  it("test_loading_skeleton_blocks_are_aria_hidden", () => {
    mockLoading();

    renderStep4();

    expect(screen.getByTestId("contact-form-skeleton")).toHaveAttribute("aria-hidden", "true");
  });

  // The original bug: the server HTML for step 4 was empty.
  it("test_server_render_while_loading_contains_the_summary", () => {
    mockLoading();

    const html = renderToString(
      <BookingContactInfoProvider>
        <BookingStep4 {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    expect(html).toContain("Manicure");
    expect(html).toContain("Olena");
    expect(html).toMatch(SLOT_TEXT);
  });
});
