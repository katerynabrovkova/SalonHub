// @vitest-environment jsdom
/**
 * Manage page component after the inline script (docs/DECISIONS.md "Booking >
 * Guest token", decided 11.11.2026): it reads the token stored for this id
 * when the hash has none, and still reads the hash when the script could not
 * run (storage blocked). The stored-token case for this id only is also
 * covered by ManageAppointment.test.tsx; here the key comes from the shared
 * helper, so the component and the script cannot use different keys.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const guestApiRequestMock = vi.fn();
vi.mock("@/lib/api/guestClient", () => ({
  guestApiRequest: (...args: unknown[]) => guestApiRequestMock(...args),
}));

import { guestManageTokenKey } from "@/lib/security/fragmentTokenScript";

import ManageAppointment from "./ManageAppointment";

const PATH = "/appointments/42/manage";

function detail() {
  return {
    id: 42,
    status: "confirmed",
    start_datetime: "2026-11-14T14:00:00+02:00",
    end_datetime: "2026-11-14T15:30:00+02:00",
    specialist: { id: 7, name: "Яна" },
    service: { id: 3, name: "Манікюр" },
    service_price_at_booking: "120.00",
    deposit_amount: "24.00",
    amount_due_at_visit: "96.00",
    currency: "UAH",
    deposit_outcome: null,
    refund_deadline: null,
  };
}

function blockStorage() {
  const blocked = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
}

beforeEach(() => {
  guestApiRequestMock.mockReset();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", PATH);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("ManageAppointment with the inline script", () => {
  it("test_uses_the_token_the_script_stored_under_the_shared_key", async () => {
    window.sessionStorage.setItem(guestManageTokenKey(7), "tok-other");
    window.sessionStorage.setItem(guestManageTokenKey(42), "tok-stored");
    guestApiRequestMock.mockResolvedValueOnce(detail());

    render(<ManageAppointment slug="bella-demo" appointmentId={42} />);

    await screen.findByRole("heading", { name: "Ваш запис" });
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-stored",
    );
  });

  it("test_storage_blocked_still_reads_the_token_from_the_hash", async () => {
    blockStorage();
    window.history.replaceState(null, "", `${PATH}#token=tok-hash`);
    guestApiRequestMock.mockResolvedValueOnce(detail());

    render(<ManageAppointment slug="bella-demo" appointmentId={42} />);

    await screen.findByRole("heading", { name: "Ваш запис" });
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-hash",
    );
  });
});
