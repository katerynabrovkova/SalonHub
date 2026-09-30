// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 10 design details (edit name and phone)",
// point 6, "Frontend details".

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
}));

const refreshMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the component sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";

import EditProfileField from "./EditProfileField";

const mockedApiRequest = vi.mocked(apiRequest);

const ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: "Alice",
  phone: "+10000000000",
  email_verified: true,
};

const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const SESSION_ENDED_MESSAGE = "Сесія завершилась. Увійдіть знову.";
const GENERIC_SAVE_MESSAGE = "Не вдалося зберегти. Спробуйте ще раз.";

function mockAuth(me: unknown) {
  mockedUseAuth.mockReturnValue({
    me,
    loading: false,
    loggedOutDeliberately: false,
    login: vi.fn(),
    logout: vi.fn(),
    refresh: refreshMock,
  });
}

function renderName() {
  return render(
    <EditProfileField
      field="name"
      title="Змінити ім'я"
      label="Ім'я"
      inputType="text"
      maxLength={255}
    />,
  );
}

function renderPhone() {
  return render(
    <EditProfileField
      field="phone"
      title="Змінити телефон"
      label="Телефон"
      inputType="tel"
      maxLength={32}
    />,
  );
}

/** Renders the name variant, submits it unchanged, and returns once the click is done. */
async function submitName() {
  const user = userEvent.setup();
  renderName();
  await user.click(screen.getByRole("button", { name: "Зберегти" }));
}

function expectNoNavigationAndNoRefresh() {
  expect(pushMock).not.toHaveBeenCalled();
  expect(replaceMock).not.toHaveBeenCalled();
  expect(refreshMock).not.toHaveBeenCalled();
}

beforeEach(() => {
  pushMock.mockReset();
  replaceMock.mockReset();
  refreshMock.mockReset();
  mockedUseAuth.mockReset();
  mockedApiRequest.mockReset();
  mockAuth(ME);
});

describe("EditProfileField", () => {
  it("test_name_input_prefilled_with_type_text_maxlength_255_and_required", () => {
    renderName();

    const input = screen.getByLabelText("Ім'я");
    expect(input).toHaveValue("Alice");
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveAttribute("maxLength", "255");
    expect(input).toBeRequired();
  });

  it("test_phone_input_prefilled_with_type_tel_maxlength_32_and_required", () => {
    renderPhone();

    const input = screen.getByLabelText("Телефон");
    expect(input).toHaveValue("+10000000000");
    expect(input).toHaveAttribute("type", "tel");
    expect(input).toHaveAttribute("maxLength", "32");
    expect(input).toBeRequired();
  });

  it("test_renders_title_as_heading", () => {
    renderName();

    expect(screen.getByRole("heading", { name: "Змінити ім'я" })).toBeInTheDocument();
  });

  it("test_patch_body_contains_only_name", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce({ name: "Alicia", phone: "+10000000000" });

    renderName();
    const input = screen.getByLabelText("Ім'я");
    await user.clear(input);
    await user.type(input, "Alicia");
    await user.click(screen.getByRole("button", { name: "Зберегти" }));

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    const [, path, options] = mockedApiRequest.mock.calls[0] as [string, string, RequestInit];
    expect(path).toBe("/auth/me/customer/");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body as string)).toEqual({ name: "Alicia" });
  });

  it("test_patch_body_contains_only_phone", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce({ name: "Alice", phone: "+20000000000" });

    renderPhone();
    const input = screen.getByLabelText("Телефон");
    await user.clear(input);
    await user.type(input, "+20000000000");
    await user.click(screen.getByRole("button", { name: "Зберегти" }));

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    const [, path, options] = mockedApiRequest.mock.calls[0] as [string, string, RequestInit];
    expect(path).toBe("/auth/me/customer/");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body as string)).toEqual({ phone: "+20000000000" });
  });

  it("test_submit_patches_then_refreshes_then_pushes_profile_in_order", async () => {
    const callOrder: string[] = [];
    mockedApiRequest.mockImplementationOnce(async () => {
      callOrder.push("patch");
      return { name: "Alice", phone: "+10000000000" };
    });
    refreshMock.mockImplementationOnce(async () => {
      callOrder.push("refresh");
    });
    pushMock.mockImplementationOnce((url: string) => {
      callOrder.push(`push:${url}`);
    });

    await submitName();

    await waitFor(() => expect(callOrder).toEqual(["patch", "refresh", "push:/client/profile"]));
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("test_button_disabled_while_pending_and_second_click_ignored", async () => {
    const user = userEvent.setup();
    let resolvePatch: (value: unknown) => void = () => {};
    const pendingPatch = new Promise((resolve) => {
      resolvePatch = resolve;
    });
    mockedApiRequest.mockReturnValueOnce(pendingPatch);

    renderName();
    const button = screen.getByRole("button", { name: "Зберегти" });
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);

    expect(mockedApiRequest).toHaveBeenCalledTimes(1);

    resolvePatch({ name: "Alice", phone: "+10000000000" });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/client/profile"));
  });

  it("test_no_linked_customer_redirects_to_profile_and_renders_nothing", async () => {
    mockAuth({ ...ME, name: null, phone: null });

    const { container } = renderName();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/client/profile"));
    expect(container).toBeEmptyDOMElement();
    expect(mockedApiRequest).not.toHaveBeenCalled();
  });

  it("test_type_error_shows_connection_message_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await submitName();

    expect(await screen.findByRole("alert")).toHaveTextContent(CONNECTION_MESSAGE);
    expectNoNavigationAndNoRefresh();
  });

  it("test_renewal_unsure_error_shows_connection_message_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(new RenewalUnsureError());

    await submitName();

    expect(await screen.findByRole("alert")).toHaveTextContent(CONNECTION_MESSAGE);
    expectNoNavigationAndNoRefresh();
  });

  it("test_401_shows_session_ended_message_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
    );

    await submitName();

    expect(await screen.findByRole("alert")).toHaveTextContent(SESSION_ENDED_MESSAGE);
    expectNoNavigationAndNoRefresh();
  });

  it("test_404_replaces_to_profile", async () => {
    mockedApiRequest.mockRejectedValueOnce(new ApiError(404, "not_found", "Not found."));

    await submitName();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/client/profile"));
    expect(pushMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("test_400_shows_generic_save_message_not_drf_text_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid", "Request failed.", {
        name: ["This field may not be blank."],
      }),
    );

    await submitName();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(GENERIC_SAVE_MESSAGE);
    expect(alert).not.toHaveTextContent("This field may not be blank.");
    expectNoNavigationAndNoRefresh();
  });

  it("test_500_shows_generic_save_message_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "An unexpected error occurred."),
    );

    await submitName();

    expect(await screen.findByRole("alert")).toHaveTextContent(GENERIC_SAVE_MESSAGE);
    expectNoNavigationAndNoRefresh();
  });

  it("test_non_api_error_shows_generic_save_message_without_navigation", async () => {
    mockedApiRequest.mockRejectedValueOnce(new Error("boom"));

    await submitName();

    expect(await screen.findByRole("alert")).toHaveTextContent(GENERIC_SAVE_MESSAGE);
    expectNoNavigationAndNoRefresh();
  });

  it("test_back_link_points_to_profile", () => {
    renderName();

    const link = screen.getByRole("link", { name: /Профіль/ });
    expect(link).toHaveAttribute("href", "/client/profile");
    expect(link).toHaveTextContent("← Профіль");
  });
});
