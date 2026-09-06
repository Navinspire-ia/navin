import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const Welcome = () => (
  <Card>
    <CardHeader className="px-4">
      <CardTitle>Navin CRM</CardTitle>
    </CardHeader>
    <CardContent className="px-4">
      <p className="text-sm mb-4 [text-wrap:pretty]">
        Produit CRM AI-native. Cette demo tourne sur une API mock: tu peux
        explorer et modifier les donnees. Elles se reinitialisent au reload.
      </p>
      <p className="text-sm mb-4 [text-wrap:pretty]">
        La version complete utilise Supabase. Code amont MIT:{" "}
        <a
          href="https://github.com/marmelab/atomic-crm"
          className="underline hover:no-underline"
        >
          marmelab/atomic-crm
        </a>
        .
      </p>
      <p className="text-sm [text-wrap:pretty]">
        Personnalise les agents dans{" "}
        <code className="text-[12px]">.navin/apps/crm/overlay.json</code>.
      </p>
    </CardContent>
  </Card>
);
