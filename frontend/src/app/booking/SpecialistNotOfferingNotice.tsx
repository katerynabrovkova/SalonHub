/**
 * Message for SPECIALIST_DOES_NOT_OFFER_SERVICE, shared by booking step 3
 * and both step-4 forms (docs/DECISIONS.md § "Fix: the specialist must
 * perform the service").
 */
import Link from "next/link";

import { buildChooseAnotherSpecialistUrl } from "@/lib/booking/step3Errors";

export default function SpecialistNotOfferingNotice({ service }: { service: string }) {
  return (
    <div role="alert" className="flex flex-col gap-2">
      <p>Цей майстер не виконує обрану послугу.</p>
      <Link href={buildChooseAnotherSpecialistUrl(service)} className="text-blue-600 underline">
        Обрати іншого майстра
      </Link>
    </div>
  );
}
