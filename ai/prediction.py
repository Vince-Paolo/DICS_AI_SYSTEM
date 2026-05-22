from sklearn.linear_model import LinearRegression

class HazardPredictor:
    def __init__(self):
        self.models = {}
        self._train_models()

    def _train_models(self):
        training_data = {
            'flood': [
                ([10, 1.0, 20, 50], 10),
                ([40, 2.5, 45, 150], 35),
                ([80, 4.0, 70, 300], 65),
                ([120, 5.5, 85, 500], 85),
                ([180, 6.0, 90, 700], 95),
            ],
            'landslide': [
                ([15, 0.5, 80, 200], 40),
                ([35, 0.8, 85, 400], 55),
                ([60, 1.0, 90, 600], 75),
                ([90, 1.5, 95, 700], 88),
                ([20, 0.3, 40, 80], 20),
            ],
            'storm': [
                ([20, 2.0, 30, 100], 25),
                ([55, 3.5, 55, 250], 50),
                ([90, 4.5, 70, 450], 70),
                ([130, 5.0, 80, 600], 85),
                ([170, 5.8, 92, 800], 94),
            ],
        }

        for hazard_type, examples in training_data.items():
            X = [features for features, score in examples]
            y = [score for features, score in examples]
            model = LinearRegression()
            model.fit(X, y)
            self.models[hazard_type] = model

    def predict(self, hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density):
        model = self.models.get(hazard_type)
        if model is None:
            hazard_type = 'flood'
            model = self.models['flood']

        X = [[rainfall_mm, river_level_m, soil_moisture_pct, population_density]]
        score = model.predict(X)[0]
        score = max(0.0, min(100.0, round(score, 1)))

        if score < 25:
            level = 'Low'
            message = 'Low hazard risk. Continue monitoring conditions.'
        elif score < 50:
            level = 'Moderate'
            message = 'Moderate hazard risk. Prepare to respond as conditions change.'
        elif score < 75:
            level = 'High'
            message = 'High hazard risk. Take action and follow precautionary procedures.'
        else:
            level = 'Severe'
            message = 'Severe hazard risk. Activate emergency response and evacuate if needed.'

        return {
            'type': hazard_type,
            'score': score,
            'level': level,
            'message': message,
            'alert': score >= 50,
        }

predictor = HazardPredictor()


def predict_hazard(hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density):
    return predictor.predict(hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density)
