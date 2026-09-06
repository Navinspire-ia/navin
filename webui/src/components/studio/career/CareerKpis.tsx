export type CareerMoneyFact = { id: string; label: string; value: string };

export type CareerMoneyGoalField = {
  label: string;
  value: string;
  suffix: string;
  saveLabel: string;
  onChange: (value: string) => void;
  onSave: () => void;
};

export type CareerMoneyBarProps = {
  /** Formatted monthly goal, empty when the user has not set a rate yet. */
  amount: string;
  /** Short line under the amount, e.g. "650 EUR / day x 18 days". */
  basis?: string;
  facts: CareerMoneyFact[];
  goalField?: CareerMoneyGoalField;
};

/**
 * The first thing the desk shows: what the stored goal is worth per month,
 * then the pipeline facts that back it. Every figure is computed from the
 * profile or from a pay line an offer actually posted.
 */
export function CareerMoneyBar({ amount, basis, facts, goalField }: CareerMoneyBarProps) {
  return (
    <div
      className="grid gap-6 rounded-2xl bg-card px-6 py-5 shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 sm:grid-cols-[minmax(0,auto)_1fr] sm:items-center sm:gap-10 dark:outline-white/10"
      data-testid="career-money-bar"
    >
      <div className="min-w-0">
        {amount ? (
          <p
            className="text-4xl font-semibold leading-none tabular-nums tracking-tight"
            data-testid="career-money-amount"
          >
            {amount}
          </p>
        ) : null}
        {amount && basis ? <p className="mt-2 text-[12px] tabular-nums text-muted-foreground">{basis}</p> : null}
        {!amount && goalField ? (
          <div className="flex flex-wrap items-end gap-2" data-testid="career-money-goal">
            <label className="grid min-w-[10rem] gap-1 text-[12px] text-muted-foreground">
              {goalField.label}
              <span className="flex items-center gap-2">
                <input
                  className="min-h-10 w-28 rounded-xl bg-background px-3 text-sm tabular-nums text-foreground outline outline-1 outline-black/10 dark:outline-white/10"
                  inputMode="decimal"
                  value={goalField.value}
                  onChange={(event) => goalField.onChange(event.target.value)}
                  aria-label={goalField.label}
                />
                <span>{goalField.suffix}</span>
              </span>
            </label>
            <button
              type="button"
              className="min-h-10 rounded-xl bg-indigo-600 px-3.5 text-sm font-medium text-white"
              onClick={goalField.onSave}
            >
              {goalField.saveLabel}
            </button>
          </div>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-start gap-x-10 gap-y-4">
        {facts.map((fact) => (
          <div key={fact.id} className="min-w-[6.5rem]" data-testid={`career-money-${fact.id}`}>
            <div className="text-[12px] text-muted-foreground">{fact.label}</div>
            <div className="mt-1 text-2xl font-semibold leading-none tabular-nums">{fact.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
