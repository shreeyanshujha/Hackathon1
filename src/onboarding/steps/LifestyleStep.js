import React from 'react';
import { View } from 'react-native';
import { Card, Field, TextField, ChoiceChips, SwitchRow } from '../../components/ui';
import { DIET_OPTIONS, FREQUENCY_OPTIONS } from '../../model/profile';

export default function LifestyleStep({ draft, setDraft, errors }) {
  const l = draft.lifestyle;
  const patch = (p) => setDraft({ lifestyle: { ...l, ...p } });

  return (
    <View>
      <Field label="Diet">
        <ChoiceChips options={DIET_OPTIONS} value={l.diet} onChange={(diet) => patch({ diet })} />
      </Field>

      <Field label="Diet notes (optional)">
        <TextField
          value={l.dietNote}
          onChangeText={(dietNote) => patch({ dietNote })}
          placeholder="e.g. Cooks at home most nights, meals-on-wheels on Tuesdays"
          multiline
        />
      </Field>

      <Card>
        <SwitchRow
          label="Smokes"
          value={l.smoking.status}
          onValueChange={(status) => patch({ smoking: { status, frequency: status ? l.smoking.frequency : null } })}
        />
        {l.smoking.status && (
          <Field label="How often?" error={errors.smokingFrequency} style={{ marginTop: 12, marginBottom: 0 }}>
            <ChoiceChips
              options={FREQUENCY_OPTIONS}
              value={l.smoking.frequency}
              onChange={(frequency) => patch({ smoking: { ...l.smoking, frequency } })}
            />
          </Field>
        )}
      </Card>

      <Card>
        <SwitchRow
          label="Drinks alcohol"
          value={l.alcohol.status}
          onValueChange={(status) => patch({ alcohol: { status, frequency: status ? l.alcohol.frequency : null } })}
        />
        {l.alcohol.status && (
          <Field label="How often?" error={errors.alcoholFrequency} style={{ marginTop: 12, marginBottom: 0 }}>
            <ChoiceChips
              options={FREQUENCY_OPTIONS}
              value={l.alcohol.frequency}
              onChange={(frequency) => patch({ alcohol: { ...l.alcohol, frequency } })}
            />
          </Field>
        )}
      </Card>
    </View>
  );
}
