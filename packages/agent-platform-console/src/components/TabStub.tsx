import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// Placeholder card used by tab views whose backend lands in a later R-* PR.
// Keeps the navigation walkable while reviewers can see the IA shape.
export default function TabStub({
  title,
  pr,
  details,
}: {
  title: string;
  pr: string;
  details?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Badge variant="outline">{pr}</Badge>
        </div>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        {details ?? "Wiring lands with the referenced PR per docs/architecture/18 §2."}
      </CardContent>
    </Card>
  );
}
