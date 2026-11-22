import { MenuItem, Stack, TextField } from "@mui/material";
export type Values = { title: string; year: string; type: string };
export const FIELDS = ["title", "year", "type"] as const;
export function Form({ values, onChange }: { values: Values; onChange: (k: keyof Values, v: string) => void }) {
  return (
    <Stack spacing={2} data-testid="form">
      <TextField label="Title" value={values.title} onChange={(e) => onChange("title", e.target.value)} inputProps={{ "data-testid": "f-title" }} />
      <TextField label="Year" value={values.year} onChange={(e) => onChange("year", e.target.value)} inputProps={{ "data-testid": "f-year" }} />
      <TextField select label="Type" value={values.type} onChange={(e) => onChange("type", e.target.value)} SelectProps={{ native: true }} inputProps={{ "data-testid": "f-type" }}>
        <option value="">-</option><option value="book">book</option><option value="movie">movie</option>
      </TextField>
    </Stack>
  );
}
