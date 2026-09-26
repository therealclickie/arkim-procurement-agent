import { AppLayout } from "@/components/gofer/app-layout";
import { SidebarNav } from "@/components/gofer/sidebar-nav";
import { BuyerGate } from "@/lib/buyer-session";

export default function RunsLayout({ children }: { children: React.ReactNode }) {
  // Arc 6: behind the buyer session guard when NEXT_PUBLIC_BUYER_SESSION_V1 is on;
  // a pass-through (today's frame) when it is off.
  return (
    <BuyerGate>
      <AppLayout sidebar={<SidebarNav />}>
        {children}
      </AppLayout>
    </BuyerGate>
  );
}
