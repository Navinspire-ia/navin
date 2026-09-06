import { useGetList } from "ra-core";
import { motion, useReducedMotion } from "framer-motion";

import type { Contact, ContactNote } from "../types";
import { DashboardActivityLog } from "./DashboardActivityLog";
import { DashboardStepper } from "./DashboardStepper";
import { DealsChart } from "./DealsChart";
import { HotContacts } from "./HotContacts";
import { TasksList } from "./TasksList";
import { Welcome } from "./Welcome";

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

function DashboardSkeleton() {
  return (
    <div
      className="grid grid-cols-1 md:grid-cols-12 gap-6 mt-1"
      aria-busy="true"
      aria-label="Loading dashboard"
    >
      <div className="md:col-span-3 flex flex-col gap-4">
        <div className="h-40 animate-pulse rounded-xl bg-muted" />
        <div className="h-56 animate-pulse rounded-xl bg-muted" />
      </div>
      <div className="md:col-span-6 flex flex-col gap-6">
        <div className="h-52 animate-pulse rounded-xl bg-muted" />
        <div className="h-72 animate-pulse rounded-xl bg-muted" />
      </div>
      <div className="md:col-span-3">
        <div className="h-80 animate-pulse rounded-xl bg-muted" />
      </div>
    </div>
  );
}

export const Dashboard = () => {
  const reduceMotion = useReducedMotion();
  const {
    data: dataContact,
    total: totalContact,
    isPending: isPendingContact,
  } = useGetList<Contact>("contacts", {
    pagination: { page: 1, perPage: 1 },
  });

  const { total: totalContactNotes, isPending: isPendingContactNotes } =
    useGetList<ContactNote>("contact_notes", {
      pagination: { page: 1, perPage: 1 },
    });

  const { total: totalDeal, isPending: isPendingDeal } = useGetList<Contact>(
    "deals",
    {
      pagination: { page: 1, perPage: 1 },
    },
  );

  const isPending = isPendingContact || isPendingContactNotes || isPendingDeal;

  if (isPending) {
    return <DashboardSkeleton />;
  }

  if (!totalContact) {
    return <DashboardStepper step={1} />;
  }

  if (!totalContactNotes) {
    return <DashboardStepper step={2} contactId={dataContact?.[0]?.id} />;
  }

  return (
    <motion.div
      className="grid grid-cols-1 md:grid-cols-12 gap-6 mt-1"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
    >
      <div className="md:col-span-3">
        <div className="flex flex-col gap-4">
          {import.meta.env.VITE_IS_DEMO === "true" ? <Welcome /> : null}
          <HotContacts />
        </div>
      </div>
      <div className="md:col-span-6">
        <div className="flex flex-col gap-6">
          {totalDeal ? <DealsChart /> : null}
          <DashboardActivityLog />
        </div>
      </div>

      <div className="md:col-span-3">
        <TasksList />
      </div>
    </motion.div>
  );
};
