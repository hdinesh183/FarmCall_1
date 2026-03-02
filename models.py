from sqlalchemy import Column, Integer, String, TEXT, TIMESTAMP, ForeignKey, Date, Float, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Village(Base):
    __tablename__ = 'villages'

    id = Column(Integer, primary_key=True, index=True)
    village_name = Column(String(100))
    mandal = Column(String(100))
    district = Column(String(100))
    state = Column(String(100))
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    # Relationships
    farmers = relationship("Farmer", back_populates="village")
    weather_data = relationship("WeatherData", back_populates="village")
    advisories = relationship("Advisory", back_populates="village")

class Farmer(Base):
    __tablename__ = 'farmers'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(15), unique=False, nullable=False, index=True)
    village_id = Column(Integer, ForeignKey('villages.id'))
    crop = Column(String(100))
    language = Column(String(50))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Establish relationships
    village = relationship("Village", back_populates="farmers")
    advisory_calls = relationship("AdvisoryCall", back_populates="farmer")

class WeatherData(Base):
    __tablename__ = 'weather_data'

    id = Column(Integer, primary_key=True, index=True)
    village_id = Column(Integer, ForeignKey('villages.id'))
    forecast_date = Column(Date, nullable=False)
    rain_probability = Column(Float)
    rain_mm = Column(Float)
    min_temperature = Column(Float)
    max_temperature = Column(Float)
    wind_speed = Column(Float)
    humidity = Column(Float)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_village_forecast', 'village_id', 'forecast_date'),
    )

    # Establish relationship to the Village model
    village = relationship("Village", back_populates="weather_data")

class Advisory(Base):
    __tablename__ = 'advisories'

    id = Column(Integer, primary_key=True, index=True)
    village_id = Column(Integer, ForeignKey('villages.id', ondelete='CASCADE'), nullable=False)
    forecast_start_date = Column(Date, nullable=False)
    forecast_end_date = Column(Date, nullable=False)
    risk_level = Column(String(50))
    risk_type = Column(String(100))
    advisory_text = Column(TEXT, nullable=False)
    audio_filename = Column(TEXT)
    language = Column(String(50), nullable=False)
    ai_model_used = Column(String(100))
    trigger_type = Column(String(20), default='auto')
    call_status = Column(String(50), default='pending')
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_advisory_village_date', 'village_id', 'forecast_start_date'),
    )

    # Establish relationships
    village = relationship("Village", back_populates="advisories")
    calls = relationship("AdvisoryCall", back_populates="advisory")

class AdvisoryCall(Base):
    __tablename__ = 'advisory_calls'

    id = Column(Integer, primary_key=True, index=True)
    advisory_id = Column(Integer, ForeignKey('advisories.id'))
    farmer_id = Column(Integer, ForeignKey('farmers.id'))
    call_status = Column(String(50))
    call_time = Column(TIMESTAMP)

    # Establish relationships
    advisory = relationship("Advisory", back_populates="calls")
    farmer = relationship("Farmer", back_populates="advisory_calls")
