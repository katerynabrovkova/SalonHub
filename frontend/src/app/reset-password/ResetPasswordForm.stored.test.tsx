// @vitest-environment jsdom
/**
 * Reset-password form after the inline script (docs/DECISIONS.md "Auth and
 * accounts > Frontend session handling", "Booking > Guest token", decided
 * 11.11.2026): with no link in the hash it uses the link the script stored
 * under the page's own key; it still reads the hash when the script could not
 * run (storage blocked).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/app/AuthContext", () => ({
  useAuth: () => ({ logout: vi.fn() }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "@/lib/api/client";
import { PASSWORD_RESET_LINK_KEY } from "@/lib/security/fragmentTokenScript";

import ResetPasswordForm from "./ResetPasswordForm";

const mockedApiRequest = vi.mocked(apiRequest);

const UID = "MQ";
const TOKEN = "cz1x2y-0123456789abcdef";
const CHECK_PATH = "/auth/password-reset/check/";
const NEW_LABEL = "Новий пароль";
const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";

function checkBodies() {
  return mockedApiRequest.mock.calls
    .filter(([, path]) => path === CHECK_PATH)
    .map(([, , options]) => JSON.parse((options as RequestInit).body as string));
}

function blockStorage() {
  const blocked = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
}

let originalUrl: string;

beforeEach(() => {
  originalUrl = window.location.href;
  window.sessionStorage.clear();
  mockedApiRequest.mockReset();
  mockedApiRequest.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", originalUrl);
});

describe("ResetPasswordForm with a stored link", () => {
  it("test_no_hash_uses_the_link_the_script_stored_and_shows_the_form", async () => {
    window.history.replaceState(null, "", "/reset-password");
    window.sessionStorage.setItem(
      PASSWORD_RESET_LINK_KEY,
      new URLSearchParams({ uid: UID, token: TOKEN }).toString(),
    );

    render(<ResetPasswordForm slug="bella-demo" />);

    expect(await screen.findByLabelText(NEW_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
    expect(checkBodies()).toEqual([{ uid: UID, token: TOKEN }]);
  });

  it("test_no_hash_and_nothing_stored_shows_the_invalid_link_state_without_a_request", async () => {
    window.history.replaceState(null, "", "/reset-password");

    render(<ResetPasswordForm slug="bella-demo" />);

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(mockedApiRequest).not.toHaveBeenCalled();
  });

  it("test_storage_blocked_still_reads_the_link_from_the_hash", async () => {
    blockStorage();
    window.history.replaceState(null, "", `/reset-password#uid=${UID}&token=${TOKEN}`);

    render(<ResetPasswordForm slug="bella-demo" />);

    expect(await screen.findByLabelText(NEW_LABEL)).toBeInTheDocument();
    await waitFor(() => expect(checkBodies()).toEqual([{ uid: UID, token: TOKEN }]));
  });
});
