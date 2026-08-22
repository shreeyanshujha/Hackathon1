import React from 'react';
import { View } from 'react-native';
import { Field, ChoiceChips, TagEditor, InfoBanner } from '../../components/ui';
import { MOBILITY_OPTIONS, HOBBY_SUGGESTIONS } from '../../model/profile';

export default function HobbiesMobilityStep({ draft, setDraft, errors }) {
  return (
    <View>
      <InfoBanner>
        Hobbies tell the system what active time normally looks like. Mobility level sets
        how sensitive fall detection should be.
      </InfoBanner>

      <Field label="Hobbies & interests" hint="Tap any that fit, or add your own.">
        <TagEditor
          tags={draft.hobbies}
          onChange={(hobbies) => setDraft({ hobbies })}
          suggestions={HOBBY_SUGGESTIONS}
          placeholder="Add a hobby…"
        />
      </Field>

      <Field label="Mobility level" error={errors.mobilityLevel}>
        <ChoiceChips
          options={MOBILITY_OPTIONS}
          value={draft.mobilityLevel}
          onChange={(mobilityLevel) => setDraft({ mobilityLevel })}
        />
      </Field>
    </View>
  );
}
