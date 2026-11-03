import { describe, expect, it } from "vitest";

import { statusText } from "./statusText";

// docs/DECISIONS.md § "Stage 17 block D" (working indicator) and § "Stage 17
// sub-step order", 17.2a (step keys are the tool names) and 17.5a.
describe("statusText", () => {
  it.each([
    { step: "list_services", text: "Дивлюся послуги…" },
    { step: "check_availability", text: "Перевіряю вільний час…" },
    { step: "get_specialist_info", text: "Дивлюся інформацію про майстрів…" },
    { step: "get_faq", text: "Шукаю відповідь…" },
    { step: "get_salon_info", text: "Уточнюю деталі салону…" },
    { step: "propose_booking", text: "Готую пропозицію…" },
  ])("test_step_maps_to_its_text_($step)", ({ step, text }) => {
    expect(statusText(step)).toBe(text);
  });

  it("test_unknown_step_returns_null", () => {
    expect(statusText("mystery_tool")).toBeNull();
  });
});
