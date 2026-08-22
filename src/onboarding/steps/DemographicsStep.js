import React from 'react';
import { View } from 'react-native';
import { Field, TextField, ChoiceChips } from '../../components/ui';
import { SEX_OPTIONS, LIVING_SITUATION_OPTIONS } from '../../model/profile';
import { parseDob, ageFromDob } from '../../utils/datetime';

export default function DemographicsStep({ draft, setDraft, errors }) {
  const d = draft.demographics;
  const patch = (p) => setDraft({ demographics: { ...d, ...p } });
  const dobIso = parseDob(d.dobText);

  return (
    <View>
      <Field label="Full name" error={errors.name}>
        <TextField
          value={d.name}
          onChangeText={(name) => patch({ name })}
          placeholder="e.g. Margaret Wilson"
          autoCapitalize="words"
          error={!!errors.name}
        />
      </Field>

      <Field label="Sex" error={errors.sex}>
        <ChoiceChips options={SEX_OPTIONS} value={d.sex} onChange={(sex) => patch({ sex })} />
      </Field>

      <Field
        label="Date of birth"
        error={errors.dob}
        hint={dobIso ? `Age ${ageFromDob(dobIso)}` : 'DD/MM/YYYY'}
      >
        <TextField
          value={d.dobText}
          onChangeText={(dobText) => patch({ dobText })}
          placeholder="23/05/1948"
          autoCorrect={false}
          error={!!errors.dob}
        />
      </Field>

      <Field label="Living situation" error={errors.livingSituation}>
        <ChoiceChips
          options={LIVING_SITUATION_OPTIONS}
          value={d.livingSituation}
          onChange={(livingSituation) => patch({ livingSituation })}
        />
      </Field>
    </View>
  );
}
