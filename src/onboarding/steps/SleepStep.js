import React from 'react';
import { View } from 'react-native';
import { Field, TextField, TimeField, InfoBanner } from '../../components/ui';

export default function SleepStep({ draft, setDraft, errors }) {
  const s = draft.sleep;
  const patch = (p) => setDraft({ sleep: { ...s, ...p } });

  return (
    <View>
      <InfoBanner>
        Knowing your normal sleep hours stops long, still nights being mistaken for an
        emergency — and means stillness outside them gets noticed.
      </InfoBanner>

      <Field label="Typical wake time" error={errors.typicalWake}>
        <TimeField
          value={s.typicalWake}
          onChangeText={(typicalWake) => patch({ typicalWake })}
          placeholder="e.g. 7:30 am"
          error={!!errors.typicalWake}
        />
      </Field>

      <Field label="Typical bedtime" error={errors.typicalSleep}>
        <TimeField
          value={s.typicalSleep}
          onChangeText={(typicalSleep) => patch({ typicalSleep })}
          placeholder="e.g. 9:30 pm"
          error={!!errors.typicalSleep}
        />
      </Field>

      <Field
        label="Nap pattern (optional)"
        hint="Regular daytime naps are important — they look like stillness to the sensor."
      >
        <TextField
          value={s.napPattern}
          onChangeText={(napPattern) => patch({ napPattern })}
          placeholder="e.g. Usually naps 1–2 pm after lunch"
          multiline
        />
      </Field>
    </View>
  );
}
